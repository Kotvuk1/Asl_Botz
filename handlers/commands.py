import logging
from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from config import settings
from core.db import AsyncSessionFactory
from core.llm import groq_router
from core.memory import (
    clear_history,
    delete_memory,
    format_memory_context,
    get_history,
    save_memory,
)
from core.whitelist import add_user as wl_add, remove_user as wl_remove
from database.models import User
from handlers.messages import _think_mode, _think_session_start

logger = logging.getLogger(__name__)
router = Router(name="commands")


def _owner_only(user_id: int) -> bool:
    return user_id == settings.owner_id


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    name = message.from_user.first_name or "друг"
    await message.answer(
        f"👋 Привет, <b>{name}</b>!\n\n"
        f"Я <b>{settings.bot_name}</b> ({settings.bot_short_name}) — твой личный ИИ-помощник.\n\n"
        "Просто пиши или надиктуй — я пойму:\n"
        "• «сегодня надо купить продукты» → добавлю задачу\n"
        "• «план на завтра» → покажу задачи на завтра\n"
        "• «напомни через 2 часа встреча» → поставлю напоминание\n"
        "• «сделал зарядку» → отмечу привычку\n"
        "• «хочу выучить Python к сентябрю» → добавлю цель\n\n"
        "Работаю на русском, казахском и английском 🌐",
        parse_mode="HTML",
    )


# ── /clear ────────────────────────────────────────────────────────────────────

@router.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        count = await clear_history(session, message.from_user.id)
    await message.answer(
        f"🗑 История диалога очищена ({count} сообщений).\n"
        "Начинаем с чистого листа!"
    )


# ── /memory ───────────────────────────────────────────────────────────────────

@router.message(Command("memory"))
async def cmd_memory(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        ctx = await format_memory_context(session, message.from_user.id)
    await message.answer(
        f"<b>🧠 Что я знаю о тебе:</b>\n\n{ctx}",
        parse_mode="HTML",
    )


@router.message(Command("forget"))
async def cmd_forget(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip()
    if not key:
        await message.answer("Укажи ключ: /forget <b>ключ</b>", parse_mode="HTML")
        return
    async with AsyncSessionFactory() as session:
        removed = await delete_memory(session, message.from_user.id, key)
    if removed:
        await message.answer(f"🗑 Забыл: <b>{key}</b>", parse_mode="HTML")
    else:
        await message.answer(f"Факт <b>{key}</b> не найден.", parse_mode="HTML")


# ── /think ────────────────────────────────────────────────────────────────────

@router.message(Command("think"))
async def cmd_think(message: Message) -> None:
    uid = message.from_user.id
    if uid in _think_mode:
        _think_mode.discard(uid)
        start_time = _think_session_start.pop(uid, None)
        await message.answer(
            "💡 Режим <b>«Думаем вслух»</b> выключен.\n"
            "<i>Сохраняю выводы сессии...</i>",
            parse_mode="HTML",
        )
        await _save_think_conclusions(message, start_time)
    else:
        _think_mode.add(uid)
        _think_session_start[uid] = datetime.now(timezone.utc)
        await message.answer(
            "🧠 Режим <b>«Думаем вслух»</b> включён.\n\n"
            "Говори что думаешь — буду задавать вопросы, "
            "а не давать готовые ответы. Помогу разобраться самому.\n\n"
            "Чтобы выйти — /think ещё раз.",
            parse_mode="HTML",
        )


async def _save_think_conclusions(message: Message, start_time) -> None:
    """Ask LLM to extract conclusions from the think session and save to memory."""
    uid = message.from_user.id
    try:
        async with AsyncSessionFactory() as session:
            history = await get_history(session, uid, limit=30)

        if not history:
            await message.answer("Возвращаюсь к обычному режиму.")
            return

        system_prompt = (
            "Проанализируй этот диалог «Думаем вслух» и извлеки ключевые выводы/инсайты.\n"
            "Ответь ТОЛЬКО в формате (максимум 3 вывода):\n"
            "вывод 1 = <краткая формулировка>\n"
            "вывод 2 = <краткая формулировка>\n"
            "вывод 3 = <краткая формулировка>\n"
            "Если выводов меньше — напиши меньше строк. Не добавляй ничего лишнего."
        )
        summary = await groq_router.summarize(history, system_prompt)

        saved_count = 0
        date_str = datetime.now(timezone.utc).strftime("%d.%m")
        async with AsyncSessionFactory() as session:
            for line in summary.splitlines():
                if "=" in line:
                    key_raw, _, val = line.partition("=")
                    key = key_raw.strip()[:128]
                    val = val.strip()[:500]
                    if key and val:
                        full_key = f"мысли {date_str} — {key}"
                        await save_memory(session, uid, full_key, val)
                        saved_count += 1

        if saved_count:
            await message.answer(
                f"💾 Сохранил <b>{saved_count}</b> вывода(ов) из сессии в память.\n"
                "Посмотреть: /memory",
                parse_mode="HTML",
            )
        else:
            await message.answer("Возвращаюсь к обычному режиму.")
    except Exception as e:
        logger.error("Failed to save think conclusions: %s", e)
        await message.answer("Возвращаюсь к обычному режиму.")


# ── Owner: whitelist management ───────────────────────────────────────────────

@router.message(Command("adduser"))
async def cmd_adduser(message: Message, command: CommandObject) -> None:
    if not _owner_only(message.from_user.id):
        await message.answer("⛔ Только для владельца.")
        return
    uid_str = (command.args or "").strip()
    if not uid_str.isdigit():
        await message.answer("Укажи Telegram ID: /adduser <b>12345678</b>", parse_mode="HTML")
        return
    uid = int(uid_str)
    async with AsyncSessionFactory() as session:
        user = await session.get(User, uid)
        if user is None:
            user = User(id=uid, is_whitelisted=True)
            session.add(user)
        else:
            user.is_whitelisted = True
        await session.commit()
    wl_add(uid)
    await message.answer(
        f"✅ Пользователь <code>{uid}</code> добавлен в whitelist.", parse_mode="HTML"
    )


@router.message(Command("removeuser"))
async def cmd_removeuser(message: Message, command: CommandObject) -> None:
    if not _owner_only(message.from_user.id):
        await message.answer("⛔ Только для владельца.")
        return
    uid_str = (command.args or "").strip()
    if not uid_str.isdigit():
        await message.answer("Укажи Telegram ID: /removeuser <b>12345678</b>", parse_mode="HTML")
        return
    uid = int(uid_str)
    if uid == settings.owner_id:
        await message.answer("Нельзя удалить владельца из whitelist.")
        return
    async with AsyncSessionFactory() as session:
        user = await session.get(User, uid)
        if user:
            user.is_whitelisted = False
            await session.commit()
            wl_remove(uid)
            await message.answer(
                f"✅ Пользователь <code>{uid}</code> удалён из whitelist.", parse_mode="HTML"
            )
        else:
            await message.answer("Пользователь не найден.")


@router.message(Command("users"))
async def cmd_users(message: Message) -> None:
    if not _owner_only(message.from_user.id):
        await message.answer("⛔ Только для владельца.")
        return
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(User).where(User.is_whitelisted == True)  # noqa: E712
        )
        users = result.scalars().all()
    if not users:
        await message.answer("Whitelist пуст.")
        return
    lines = [f"👥 <b>Whitelist ({len(users)} чел.):</b>\n"]
    for u in users:
        name = " ".join(filter(None, [u.first_name, u.last_name])) or "—"
        uname = f"@{u.username}" if u.username else "нет username"
        lines.append(f"• <code>{u.id}</code> {name} ({uname})")
    await message.answer("\n".join(lines), parse_mode="HTML")
