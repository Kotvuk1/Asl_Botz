import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Filter
from aiogram.types import Message

from config import settings
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
from core.tools import (
    log_mood,
    get_mood_stats,
    add_journal,
    format_mood_stats,
    format_breathing_exercise,
    get_mood_context,
    BREATHING_EXERCISES,
)
from core.utils import current_datetime_str
from core.voice import transcribe_voice

logger = logging.getLogger(__name__)
router = Router(name="messages")

# Animated thinking frames
_THINKING_FRAMES = [
    "🤔 <i>Думаю</i>",
    "🤔 <i>Думаю.</i>",
    "🤔 <i>Думаю..</i>",
    "🤔 <i>Думаю...</i>",
]

# Regex to extract ACTION tags from LLM response
_ACTION_RE = re.compile(
    r"\[ACTION:(mood|journal|breathe|stats)"    # action type
    r"(?::(-?\d+))?"                            # optional score (for mood)
    r"(?::([^\]]*))?"                           # optional text/notes/technique
    r"\]",
    re.IGNORECASE,
)


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


def _extract_actions(text: str):
    """
    Extract ACTION tags from LLM reply.
    Returns (cleaned_text, list of (action_type, score_or_technique, notes)).
    """
    actions = []
    for m in _ACTION_RE.finditer(text):
        action_type = m.group(1).lower()
        arg1 = m.group(2)  # score for mood, None otherwise
        arg2 = m.group(3)  # notes for mood/journal, technique for breathe
        actions.append((action_type, arg1, arg2))

    # Remove action tags from text
    cleaned = _ACTION_RE.sub("", text).strip()
    return cleaned, actions


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


# ── Action handling ───────────────────────────────────────────────────────────

async def _handle_actions(
    actions: list,
    user_id: int,
    message: Message,
) -> Optional[str]:
    """
    Execute parsed ACTION tags.
    Returns extra text to prepend/append to the LLM reply, or None.
    """
    extra_parts = []

    for action_type, arg1, arg2 in actions:
        try:
            if action_type == "mood":
                # [ACTION:mood:7:notes]
                score_str = arg1
                notes = arg2.strip() if arg2 else None
                if score_str and score_str.lstrip("-").isdigit():
                    score = max(1, min(10, int(score_str)))
                    async with AsyncSessionFactory() as session:
                        await log_mood(session, user_id, score, notes)
                    bar = "▓" * score + "░" * (10 - score)
                    extra_parts.append(f"<code>Настроение записано: [{bar}] {score}/10</code>")
                    logger.info("ACTION:mood executed for user %d score=%d", user_id, score)

            elif action_type == "journal":
                # [ACTION:journal:text]
                content = arg2.strip() if arg2 else (arg1 or "")
                if content:
                    async with AsyncSessionFactory() as session:
                        await add_journal(session, user_id, content=content)
                    extra_parts.append("<code>Запись добавлена в дневник.</code>")
                    logger.info("ACTION:journal executed for user %d", user_id)

            elif action_type == "breathe":
                # [ACTION:breathe:478] or [ACTION:breathe:box]
                technique = (arg2 or arg1 or "calm").strip().lower()
                if technique not in BREATHING_EXERCISES:
                    technique = "calm"
                exercise_text = format_breathing_exercise(technique)
                extra_parts.append(exercise_text)
                logger.info("ACTION:breathe executed for user %d technique=%s", user_id, technique)

            elif action_type == "stats":
                # [ACTION:stats]
                async with AsyncSessionFactory() as session:
                    stats = await get_mood_stats(session, user_id, days=30)
                extra_parts.append(format_mood_stats(stats))
                logger.info("ACTION:stats executed for user %d", user_id)

        except Exception as e:
            logger.error("Action %s failed for user %d: %s", action_type, user_id, e)

    return "\n\n".join(extra_parts) if extra_parts else None


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
        mood_ctx = await get_mood_context(session, user.id)
        now_str = current_datetime_str()

    try:
        reply = await groq_router.chat(
            messages=history,
            memory_context=memory_ctx,
            current_datetime=now_str,
            mood_context=mood_ctx,
        )
    except RuntimeError as e:
        anim_task.cancel()
        logger.error("LLM error for user %d: %s", user.id, e)
        await thinking_msg.edit_text(
            "Все AI-ключи временно недоступны (превышен лимит).\n"
            "Подожди 1-2 минуты и попробуй снова."
        )
        return
    except Exception as e:
        anim_task.cancel()
        logger.exception("Unexpected LLM error for user %d: %s", user.id, e)
        await thinking_msg.edit_text("Что-то пошло не так. Попробуй ещё раз.")
        return
    finally:
        anim_task.cancel()

    # Save raw LLM response to history
    async with AsyncSessionFactory() as session:
        await add_message(session, user.id, "assistant", reply)

    # Extract memories in background — doesn't block the response
    msg_date: Optional[datetime] = message.date if message.date else None
    asyncio.create_task(_bg_extract_memories(user.id, text, reply, msg_date))

    # ── Action detection & execution ──────────────────────────────────────
    reply_text, actions = _extract_actions(reply)

    extra_content = None
    if actions:
        extra_content = await _handle_actions(actions, user.id, message)

    # Build final reply
    if extra_content and reply_text:
        final_reply = f"{reply_text}\n\n{extra_content}"
    elif extra_content:
        final_reply = extra_content
    else:
        final_reply = reply_text or reply

    final_reply = _clean_reply(final_reply)
    try:
        await thinking_msg.edit_text(final_reply, parse_mode="HTML")
    except Exception:
        plain = re.sub(r"<[^>]+>", "", final_reply)
        try:
            await thinking_msg.edit_text(plain, parse_mode=None)
        except Exception as e:
            logger.error("Failed to send reply to user %d: %s", user.id, e)


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
            "Не смог распознать голос. Попробуй ещё раз или напиши текстом."
        )
        return

    if not text:
        await status_msg.edit_text("Голос не распознан (тишина или шум).")
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
            "Я работаю с текстом и голосовыми сообщениями. Напиши или надиктуй! 💙"
        )
        return

    await _process_text(message, text)
