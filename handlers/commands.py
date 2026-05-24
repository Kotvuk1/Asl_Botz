import logging
from typing import Optional

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import select, delete

from config import settings
from core.db import AsyncSessionFactory
from core.memory import (
    clear_history,
    format_memory_context,
    get_memories,
    delete_memory,
    get_or_create_user,
)
from core.tools import (
    add_task, get_tasks, get_all_tasks, complete_task, set_in_progress,
    delete_task, format_tasks_list, format_today_plan,
)
from database.models import User, Memory

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
        "Я умею:\n"
        "• 💬 Отвечать на вопросы и вести беседу\n"
        "• 🧠 Помнить важную информацию о тебе\n"
        "• ✅ Управлять списком задач\n"
        "• 🌐 Работать на русском, казахском и английском\n\n"
        "Просто напиши мне что-нибудь! Или /help для списка команд.",
        parse_mode="HTML",
    )


# ── /help ─────────────────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    owner_section = ""
    if _owner_only(message.from_user.id):
        owner_section = (
            "\n\n<b>👑 Команды владельца:</b>\n"
            "/adduser &lt;id&gt; — добавить в whitelist\n"
            "/removeuser &lt;id&gt; — убрать из whitelist\n"
            "/users — список разрешённых пользователей"
        )

    await message.answer(
        f"<b>📖 Команды {settings.bot_name}:</b>\n\n"
        "<b>Основные:</b>\n"
        "/start — Приветствие\n"
        "/help — Это сообщение\n"
        "/clear — Очистить историю диалога\n\n"
        "<b>🧠 Память:</b>\n"
        "/memory — Показать сохранённые факты\n"
        "/remember &lt;ключ&gt; = &lt;значение&gt; — Запомнить факт\n"
        "/forget &lt;ключ&gt; — Забыть факт\n\n"
        "<b>📅 План / Задачи:</b>\n"
        "/today — План на сегодня (3 секции)\n"
        "/tasks — Список активных задач\n"
        "/addtask &lt;задача&gt; — Добавить задачу\n"
        "/progress &lt;id&gt; — Взять в работу\n"
        "/done &lt;id&gt; — Отметить выполненной\n"
        "/deltask &lt;id&gt; — Удалить задачу"
        + owner_section,
        parse_mode="HTML",
    )


# ── /clear ────────────────────────────────────────────────────────────────────

@router.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        count = await clear_history(session, message.from_user.id)
    await message.answer(
        f"🗑 История диалога очищена ({count} сообщений удалено).\n"
        "Начинаем с чистого листа!"
    )


# ── /memory ───────────────────────────────────────────────────────────────────

@router.message(Command("memory"))
async def cmd_memory(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        ctx = await format_memory_context(session, message.from_user.id)
    await message.answer(
        f"<b>🧠 Моя память о тебе:</b>\n\n{ctx}",
        parse_mode="HTML",
    )


@router.message(Command("remember"))
async def cmd_remember(message: Message, command: CommandObject) -> None:
    args = command.args
    if not args or "=" not in args:
        await message.answer(
            "Формат: /remember <b>ключ</b> = <b>значение</b>\n"
            "Пример: /remember имя = Асылхан",
            parse_mode="HTML",
        )
        return
    key, _, value = args.partition("=")
    key = key.strip()[:128]
    value = value.strip()[:1000]
    if not key or not value:
        await message.answer("Ключ и значение не могут быть пустыми.")
        return
    from core.memory import save_memory
    async with AsyncSessionFactory() as session:
        await save_memory(session, message.from_user.id, key, value)
    await message.answer(f"✅ Запомнил: <b>{key}</b> = {value}", parse_mode="HTML")


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


# ── /today ────────────────────────────────────────────────────────────────────

@router.message(Command("today"))
async def cmd_today(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        tasks = await get_all_tasks(session, message.from_user.id)
    await message.answer(format_today_plan(tasks), parse_mode="HTML")


# ── /tasks ────────────────────────────────────────────────────────────────────

@router.message(Command("tasks"))
async def cmd_tasks(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        tasks = await get_tasks(session, message.from_user.id, only_pending=True)
    await message.answer(format_tasks_list(tasks), parse_mode="HTML")


@router.message(Command("addtask"))
async def cmd_addtask(message: Message, command: CommandObject) -> None:
    title = (command.args or "").strip()
    if not title:
        await message.answer("Укажи задачу: /addtask <b>название</b>", parse_mode="HTML")
        return
    async with AsyncSessionFactory() as session:
        task = await add_task(session, message.from_user.id, title[:512])
    await message.answer(f"✅ Задача добавлена: <b>#{task.id}</b> {task.title}", parse_mode="HTML")


@router.message(Command("progress"))
async def cmd_progress(message: Message, command: CommandObject) -> None:
    task_id_str = (command.args or "").strip()
    if not task_id_str.isdigit():
        await message.answer("Укажи номер: /progress <b>id</b>", parse_mode="HTML")
        return
    async with AsyncSessionFactory() as session:
        ok = await set_in_progress(session, message.from_user.id, int(task_id_str))
    if ok:
        await message.answer(f"🔄 Задача <b>#{task_id_str}</b> — <b>В ПРОЦЕССЕ</b>", parse_mode="HTML")
    else:
        await message.answer(f"Задача #{task_id_str} не найдена.")


@router.message(Command("done"))
async def cmd_done(message: Message, command: CommandObject) -> None:
    task_id_str = (command.args or "").strip()
    if not task_id_str.isdigit():
        await message.answer("Укажи номер: /done <b>id</b>", parse_mode="HTML")
        return
    async with AsyncSessionFactory() as session:
        ok = await complete_task(session, message.from_user.id, int(task_id_str))
    if ok:
        await message.answer(f"✅ Задача #{task_id_str} выполнена!")
    else:
        await message.answer(f"Задача #{task_id_str} не найдена.")


@router.message(Command("deltask"))
async def cmd_deltask(message: Message, command: CommandObject) -> None:
    task_id_str = (command.args or "").strip()
    if not task_id_str.isdigit():
        await message.answer("Укажи номер: /deltask <b>id</b>", parse_mode="HTML")
        return
    async with AsyncSessionFactory() as session:
        ok = await delete_task(session, message.from_user.id, int(task_id_str))
    if ok:
        await message.answer(f"🗑 Задача #{task_id_str} удалена.")
    else:
        await message.answer(f"Задача #{task_id_str} не найдена.")


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
    await message.answer(f"✅ Пользователь <code>{uid}</code> добавлен в whitelist.", parse_mode="HTML")


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
            await message.answer(
                f"✅ Пользователь <code>{uid}</code> удалён из whitelist.",
                parse_mode="HTML",
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
