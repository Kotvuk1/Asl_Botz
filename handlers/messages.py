import asyncio
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
from core.utils import current_datetime_str

logger = logging.getLogger(__name__)
router = Router(name="messages")

# Фразы для индикатора "думаю" — чередуются анимированно
_THINKING_FRAMES = [
    "🤔 <i>Думаю</i>",
    "🤔 <i>Думаю.</i>",
    "🤔 <i>Думаю..</i>",
    "🤔 <i>Думаю...</i>",
]


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


async def _animate_thinking(thinking_msg: Message, chat_id: int) -> None:
    """Анимирует сообщение 'Думаю...' пока работает LLM."""
    i = 0
    while True:
        await asyncio.sleep(1.2)
        i = (i + 1) % len(_THINKING_FRAMES)
        try:
            await thinking_msg.edit_text(_THINKING_FRAMES[i], parse_mode="HTML")
            await thinking_msg.bot.send_chat_action(chat_id, "typing")
        except Exception:
            break


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

    # Сразу показываем видимое сообщение в чате
    await message.bot.send_chat_action(message.chat.id, "typing")
    thinking_msg = await message.answer("🤔 <i>Думаю...</i>", parse_mode="HTML")

    # Запускаем анимацию в фоне
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
        now_str = current_datetime_str()

    try:
        reply = await groq_router.chat(
            messages=history,
            memory_context=memory_ctx,
            current_datetime=now_str,
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

    # Сохраняем ответ
    async with AsyncSessionFactory() as session:
        await add_message(session, user.id, "assistant", reply)
        await extract_and_save_memories(session, user.id, text, reply)

    # Заменяем "Думаю..." на готовый ответ
    try:
        await thinking_msg.edit_text(reply, parse_mode="HTML")
    except Exception:
        # Если ответ содержит невалидный HTML — отправляем без разметки
        await thinking_msg.edit_text(reply)
