import logging

from aiogram import Router
from aiogram.filters import Filter
from aiogram.types import Message

from config import settings
from core.db import AsyncSessionFactory
from core.llm import groq_router
from core.memory import (
    add_message,
    extract_and_save_memories,
    format_memory_context,
    get_history,
    get_or_create_user,
)
from core.utils import current_datetime_str, truncate

logger = logging.getLogger(__name__)
router = Router(name="messages")


class IsWhitelisted(Filter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id in settings.allowed_user_ids


class IsNotWhitelisted(Filter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id not in settings.allowed_user_ids


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


# ── Main chat handler ─────────────────────────────────────────────────────────

@router.message(IsWhitelisted())
async def handle_message(message: Message) -> None:
    user = message.from_user
    text = message.text or message.caption

    if not text:
        await message.answer(
            "Я пока работаю только с текстом. Отправь мне текстовое сообщение!"
        )
        return

    # Show typing indicator
    await message.bot.send_chat_action(message.chat.id, "typing")

    async with AsyncSessionFactory() as session:
        # Ensure user exists in DB
        await get_or_create_user(
            session,
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
        )

        # Save user message
        await add_message(session, user.id, "user", text)

        # Build context
        history = await get_history(session, user.id)
        memory_ctx = await format_memory_context(session, user.id)
        now_str = current_datetime_str()

    try:
        await message.bot.send_chat_action(message.chat.id, "typing")
        reply = await groq_router.chat(
            messages=history,
            memory_context=memory_ctx,
            current_datetime=now_str,
        )
    except RuntimeError as e:
        logger.error("LLM error for user %d: %s", user.id, e)
        await message.answer(
            "😔 Все AI-ключи временно недоступны (превышен лимит).\n"
            "Подожди 1-2 минуты и попробуй снова."
        )
        return
    except Exception as e:
        logger.exception("Unexpected LLM error for user %d: %s", user.id, e)
        await message.answer(
            "⚠️ Что-то пошло не так. Попробуй ещё раз."
        )
        return

    # Save assistant reply
    async with AsyncSessionFactory() as session:
        await add_message(session, user.id, "assistant", reply)
        # Check if user asked to remember something
        await extract_and_save_memories(session, user.id, text, reply)

    await message.answer(reply, parse_mode="HTML")
