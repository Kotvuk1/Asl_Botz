import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Filter
from aiogram.types import Message

from aiogram.types import BufferedInputFile

from config import settings
from core.action_executor import execute_action, extract_all_actions
from core.whitelist import is_allowed
from core.db import AsyncSessionFactory
from core.llm import groq_router
from core.memory import (
    add_message,
    extract_and_save_memories,
    format_memory_context,
    get_history,
    get_or_create_user,
)
from core.tools import get_goals_context, get_habits_context, get_tasks_context
from core.utils import current_datetime_str
from core.voice import transcribe_voice

logger = logging.getLogger(__name__)
router = Router(name="messages")

# Users currently in "think out loud" mode
_think_mode: set[int] = set()

# Tracks when think-mode was started per user (for conclusion extraction)
_think_session_start: dict[int, datetime] = {}

# Animated thinking frames
_THINKING_FRAMES = [
    "🤔 <i>Думаю</i>",
    "🤔 <i>Думаю.</i>",
    "🤔 <i>Думаю..</i>",
    "🤔 <i>Думаю...</i>",
]


class IsWhitelisted(Filter):
    async def __call__(self, message: Message) -> bool:
        return is_allowed(message.from_user.id)


class IsNotWhitelisted(Filter):
    async def __call__(self, message: Message) -> bool:
        return not is_allowed(message.from_user.id)


# ── Access denied ─────────────────────────────────────────────────────────────

@router.message(IsNotWhitelisted())
async def handle_unauthorized(message: Message) -> None:
    await message.answer(
        "⛔ У вас нет доступа к этому боту.\n"
        "Обратитесь к владельцу для получения доступа."
    )
    logger.warning(
        "Unauthorized access attempt: user_id=%d username=%s",
        message.from_user.id,
        message.from_user.username,
    )


# ── Utility ───────────────────────────────────────────────────────────────────

def _clean_reply(text: str) -> str:
    """Convert Markdown artifacts to HTML."""
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text, flags=re.DOTALL)
    text = re.sub(r"^[\-\*]\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"^(\d+)\.\s+", r"\1) ", text, flags=re.MULTILINE)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text


async def _animate_thinking(thinking_msg: Message, chat_id: int) -> None:
    """Animate 'Думаю...' while LLM is working."""
    i = 0
    while True:
        await asyncio.sleep(1.2)
        i = (i + 1) % len(_THINKING_FRAMES)
        try:
            await thinking_msg.edit_text(_THINKING_FRAMES[i], parse_mode="HTML")
            await thinking_msg.bot.send_chat_action(chat_id, "typing")
        except Exception:
            break


# ── Core processing ───────────────────────────────────────────────────────────

async def _process_text(message: Message, text: str) -> None:
    """Process text (from typed or voice message) through the LLM pipeline."""
    user = message.from_user

    await message.bot.send_chat_action(message.chat.id, "typing")
    thinking_msg = await message.answer("🤔 <i>Думаю...</i>", parse_mode="HTML")

    anim_task = asyncio.create_task(
        _animate_thinking(thinking_msg, message.chat.id)
    )

    async with AsyncSessionFactory() as session:
        await get_or_create_user(
            session,
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
        )
        await add_message(session, user.id, "user", text)
        history = await get_history(session, user.id)
        memory_ctx = await format_memory_context(session, user.id)
        tasks_ctx = await get_tasks_context(session, user.id)
        goals_ctx = await get_goals_context(session, user.id)
        habits_ctx = await get_habits_context(session, user.id)
        now_str = current_datetime_str()

    try:
        reply = await groq_router.chat(
            messages=history,
            memory_context=memory_ctx,
            current_datetime=now_str,
            tasks_context=tasks_ctx,
            goals_context=goals_ctx,
            habits_context=habits_ctx,
            think_mode=user.id in _think_mode,
        )
    except RuntimeError as e:
        anim_task.cancel()
        logger.error("LLM error for user %d: %s", user.id, e)
        await thinking_msg.edit_text(
            "😔 Все AI-ключи временно недоступны (превышен лимит).\n"
            "Подожди 1-2 минуты и попробуй снова."
        )
        return
    except Exception as e:
        anim_task.cancel()
        logger.exception("Unexpected LLM error for user %d: %s", user.id, e)
        await thinking_msg.edit_text("⚠️ Что-то пошло не так. Попробуй ещё раз.")
        return
    finally:
        anim_task.cancel()

    # Save raw LLM response to history
    async with AsyncSessionFactory() as session:
        await add_message(session, user.id, "assistant", reply)

    # Extract memories in background — doesn't block the response
    # message.date is normally UTC-aware, but can be None for service messages
    msg_date: Optional[datetime] = message.date if message.date else None
    asyncio.create_task(_bg_extract_memories(user.id, text, reply, msg_date))

    # ── Action detection & execution (supports multiple [ACTION:...] tags) ──
    action_list, reply_text = extract_all_actions(reply)
    final_reply = reply_text
    action_results: list[str] = []

    for action_str in action_list:
        result = await execute_action(action_str, user.id, message.chat.id)

        if isinstance(result, tuple) and result[0] == "export":
            # Export: send as file, show LLM text as caption
            _, export_text = result
            try:
                await thinking_msg.delete()
            except Exception:
                pass
            file = BufferedInputFile(
                export_text.encode("utf-8"),
                filename="asylkhan_export.txt",
            )
            caption = reply_text or "📦 Твои данные"
            await message.answer_document(file, caption=_clean_reply(caption))
            return

        elif isinstance(result, str):
            action_results.append(result)
        # result is None → action failed silently

    if action_results:
        parts = action_results + ([reply_text] if reply_text else [])
        final_reply = "\n\n".join(parts)

    final_reply = _clean_reply(final_reply)
    try:
        await thinking_msg.edit_text(final_reply, parse_mode="HTML")
    except Exception:
        plain = re.sub(r"<[^>]+>", "", final_reply)
        await thinking_msg.edit_text(plain, parse_mode=None)


async def _bg_extract_memories(
    user_id: int,
    user_msg: str,
    assistant_reply: str,
    message_date: Optional[datetime] = None,
) -> None:
    """Background task: extract personal facts and save to memory."""
    try:
        async with AsyncSessionFactory() as session:
            await extract_and_save_memories(
                session, user_id, user_msg, assistant_reply,
                message_date=message_date,
            )
    except Exception as e:
        logger.error("BG memory extraction failed for user %d: %s", user_id, e)


# ── Voice handler ─────────────────────────────────────────────────────────────

@router.message(IsWhitelisted(), F.voice)
async def handle_voice(message: Message) -> None:
    """Transcribe voice message via Groq Whisper, then process as text."""
    status_msg = await message.answer("🎙 <i>Распознаю голос...</i>", parse_mode="HTML")

    try:
        file_info = await message.bot.get_file(message.voice.file_id)
        file_data = await message.bot.download_file(file_info.file_path)
        audio_bytes = file_data.read()
        text = await transcribe_voice(audio_bytes, filename="voice.ogg")
    except Exception as e:
        logger.error("Voice transcription error for user %d: %s", message.from_user.id, e)
        await status_msg.edit_text(
            "❌ Не смог распознать голос. Попробуй ещё раз или напиши текстом."
        )
        return

    if not text:
        await status_msg.edit_text("🎙 Голос не распознан (тишина или шум).")
        return

    # Show transcription briefly, then process
    await status_msg.edit_text(
        f"🎙 <i>Распознал:</i> {text}", parse_mode="HTML"
    )
    await asyncio.sleep(0.5)
    await status_msg.delete()

    # Process the transcribed text as a regular message
    await _process_text(message, text)


# ── Main text handler ─────────────────────────────────────────────────────────

@router.message(IsWhitelisted())
async def handle_message(message: Message) -> None:
    text = message.text or message.caption

    if not text:
        await message.answer(
            "Я работаю с текстом и голосовыми сообщениями. Напиши или надиктуй!"
        )
        return

    await _process_text(message, text)
