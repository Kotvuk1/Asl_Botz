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
    add_transaction,
    format_balance,
    format_category_stats,
    get_balance,
    get_category_stats,
    get_finance_context,
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

# Regex for action tags that LLM may embed in its response
# [ACTION:addtx:expense:5000:еда:кафе]
# [ACTION:balance]
# [ACTION:stats]
_ACTION_RE = re.compile(
    r"^\[ACTION:(?P<action>[^\]]+)\]",
    re.MULTILINE,
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
    """Convert Markdown artifacts to HTML-safe output."""
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


# ── Action parsing & execution ────────────────────────────────────────────────

def _extract_action(reply: str):
    """
    Look for the FIRST [ACTION:...] tag in the LLM reply.
    Returns (action_content, reply_without_tag).
    """
    match = _ACTION_RE.search(reply)
    if not match:
        return None, reply

    action_content = match.group("action")
    # Remove the tag line from the reply
    reply_clean = reply[: match.start()] + reply[match.end():]
    reply_clean = reply_clean.lstrip("\n").strip()
    return action_content, reply_clean


async def _execute_action(
    action_str: str,
    user_id: int,
) -> Optional[str]:
    """
    Execute an action parsed from the LLM reply.
    Returns an HTML string to prepend to the reply, or None on failure.
    """
    parts = action_str.split(":")
    action_name = parts[0].lower()

    if action_name == "addtx":
        # addtx:expense:5000:еда:кафе
        if len(parts) < 3:
            return None
        try:
            tx_type = parts[1].strip().lower()
            if tx_type not in ("income", "expense"):
                tx_type = "expense"
            amount = float(parts[2].strip().replace(",", ".").replace(" ", ""))
            category = parts[3].strip() if len(parts) > 3 else "другое"
            description = parts[4].strip() if len(parts) > 4 and parts[4].strip() else None
        except (ValueError, IndexError):
            logger.warning("Failed to parse addtx action: %s", action_str)
            return None

        try:
            async with AsyncSessionFactory() as session:
                tx = await add_transaction(
                    session,
                    user_id=user_id,
                    amount=amount,
                    tx_type=tx_type,
                    category=category,
                    description=description,
                )
            type_icon = "📥" if tx_type == "income" else "📤"
            type_label = "Доход записан" if tx_type == "income" else "Расход записан"
            desc_str = f" <i>({tx.description})</i>" if tx.description else ""
            return (
                f"{type_icon} <b>{type_label}:</b> {amount:,.0f} тг — "
                f"<b>{tx.category}</b>{desc_str}"
            )
        except Exception as e:
            logger.error("addtx action failed: %s", e)
            return None

    elif action_name == "balance":
        try:
            async with AsyncSessionFactory() as session:
                balance = await get_balance(session, user_id, days=30)
            return format_balance(balance)
        except Exception as e:
            logger.error("balance action failed: %s", e)
            return None

    elif action_name == "stats":
        try:
            async with AsyncSessionFactory() as session:
                stats = await get_category_stats(session, user_id, days=30)
            return format_category_stats(stats)
        except Exception as e:
            logger.error("stats action failed: %s", e)
            return None

    return None


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
        finance_ctx = await get_finance_context(session, user.id, days=30)
        now_str = current_datetime_str()

    try:
        reply = await groq_router.chat(
            messages=history,
            memory_context=memory_ctx,
            current_datetime=now_str,
            finance_context=finance_ctx,
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
    msg_date: Optional[datetime] = message.date if message.date else None
    asyncio.create_task(_bg_extract_memories(user.id, text, reply, msg_date))

    # ── Action detection & execution ──────────────────────────────────────
    action_str, reply_text = _extract_action(reply)
    final_reply = reply_text

    if action_str:
        action_result = await _execute_action(action_str, user.id)
        if action_result:
            if reply_text:
                final_reply = f"{action_result}\n\n{reply_text}"
            else:
                final_reply = action_result

    final_reply = _clean_reply(final_reply)
    try:
        await thinking_msg.edit_text(final_reply, parse_mode="HTML")
    except Exception:
        plain = re.sub(r"<[^>]+>", "", final_reply)
        try:
            await thinking_msg.edit_text(plain, parse_mode=None)
        except Exception as e:
            logger.error("Failed to send reply for user %d: %s", user.id, e)


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
