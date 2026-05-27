import logging
import re
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, delete

from config import settings
from core.db import AsyncSessionFactory
from core.llm import groq_router
from core.memory import (
    clear_history,
    format_memory_context,
    get_memories,
    get_history,
    delete_memory,
    get_or_create_user,
    save_memory,
)
from core.reminder_parser import (
    format_remind_at,
    parse_deadline,
    parse_recurring,
    parse_reminder,
)
from core.tools import (
    add_goal, add_habit, add_task,
    complete_goal, complete_task, delete_goal, delete_habit, delete_task,
    export_user_data,
    format_goals_list, format_habits_list, format_stats, format_tasks_list, format_today_plan,
    get_all_tasks, get_goals, get_habit_streak, get_habits, get_habits_done_today, get_tasks,
    get_user_stats,
    log_habit,
    search_goals, search_memories, search_tasks,
    set_in_progress, update_goal_progress,
)
from core.whitelist import add_user as wl_add, remove_user as wl_remove
from database.models import Reminder, User, Memory, Task, Goal, Habit
from handlers.messages import _think_mode, _think_session_start

logger = logging.getLogger(__name__)
router = Router(name="commands")


def _owner_only(user_id: int) -> bool:
    return user_id == settings.owner_id


def _confirm_kb(action: str, item_id: int, date_label: str) -> InlineKeyboardMarkup:
    """Build a yes/no inline keyboard for delete confirmation."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"✅ Удалить ({date_label})",
        callback_data=f"confirm_{action}:{item_id}",
    )
    builder.button(text="❌ Отмена", callback_data="cancel_action")
    builder.adjust(2)
    return builder.as_markup()


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
        "• ✅ Управлять задачами с приоритетами и дедлайнами\n"
        "• 🎯 Отслеживать цели и привычки\n"
        "• ⏰ Напоминать о важном\n"
        "• 🎙 Понимать голосовые сообщения\n"
        "• 🌐 Работать на русском, казахском и английском\n\n"
        "Просто напиши или надиктуй! Или /help для команд.",
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
        "<b>📋 Задачи:</b>\n"
        "/today — План на сегодня\n"
        "/tasks — Список активных задач\n"
        "/addtask &lt;задача&gt; [!high|!low] [| дедлайн] — Добавить\n"
        "/progress &lt;id&gt; — В работу\n"
        "/done &lt;id&gt; — Выполнено\n"
        "/deltask &lt;id&gt; — Удалить задачу\n\n"
        "<b>🎯 Цели:</b>\n"
        "/addgoal &lt;название&gt; [| дедлайн] — Добавить цель\n"
        "/goals — Список целей\n"
        "/goalset &lt;id&gt; &lt;0-100&gt; — Обновить прогресс\n"
        "/goaldone &lt;id&gt; — Завершить цель\n"
        "/delgoal &lt;id&gt; — Удалить цель\n\n"
        "<b>🔁 Привычки:</b>\n"
        "/addhabit &lt;название&gt; — Добавить привычку\n"
        "/habits — Список привычек со стриками\n"
        "/habitdone &lt;id&gt; — Отметить выполненной сегодня\n"
        "/delhabit &lt;id&gt; — Удалить привычку\n\n"
        "<b>⏰ Напоминания:</b>\n"
        "/remind — Одноразовое напоминание\n"
        "/repeatremind — Повторяющееся напоминание\n"
        "/reminders — Список активных\n"
        "/delremind &lt;id&gt; — Удалить\n\n"
        "<b>📊 Аналитика:</b>\n"
        "/stats [7|30|90] — Статистика продуктивности\n"
        "/search &lt;запрос&gt; — Поиск по задачам и памяти\n"
        "/export — Экспорт всех данных\n\n"
        "<b>🧠 Режимы:</b>\n"
        "/think — Режим «Думаем вслух»"
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
    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "Укажи задачу: /addtask <b>название</b> [!high|!low] [| дедлайн]\n"
            "Пример: /addtask Сдать отчёт !high | 28.05 18:00",
            parse_mode="HTML",
        )
        return

    title, priority, deadline_utc = _parse_task_args(args)
    if not title:
        await message.answer("Название задачи не может быть пустым.")
        return

    async with AsyncSessionFactory() as session:
        task = await add_task(
            session, message.from_user.id, title[:512],
            priority=priority, deadline=deadline_utc,
        )

    p_icon = {"high": " 🔴", "medium": "", "low": " ⚪"}.get(priority, "")
    dl_str = ""
    if deadline_utc:
        local_dl = deadline_utc.replace(tzinfo=timezone.utc)
        dl_str = f"\n⏰ Дедлайн: {format_remind_at(deadline_utc)}"
    await message.answer(
        f"✅ Задача добавлена: <b>#{task.id}</b> {task.title}{p_icon}{dl_str}",
        parse_mode="HTML",
    )


def _parse_task_args(args: str):
    """Parse /addtask args → (title, priority, deadline_utc | None)."""
    deadline_utc = None
    priority = "medium"

    # Extract deadline after '|'
    if "|" in args:
        title_part, _, deadline_str = args.partition("|")
        deadline_str = deadline_str.strip()
        try:
            deadline_utc = parse_deadline(deadline_str)
        except ValueError:
            pass  # ignore bad deadline string
        args = title_part.strip()

    # Extract priority flag
    for flag in ["!high", "!medium", "!low"]:
        if flag in args.lower():
            priority = flag[1:]
            args = re.sub(re.escape(flag), "", args, flags=re.I).strip()
            break

    return args.strip(), priority, deadline_utc


@router.message(Command("progress"))
async def cmd_progress(message: Message, command: CommandObject) -> None:
    task_id_str = (command.args or "").strip()
    if not task_id_str.isdigit():
        await message.answer("Укажи номер: /progress <b>id</b>", parse_mode="HTML")
        return
    async with AsyncSessionFactory() as session:
        ok = await set_in_progress(session, message.from_user.id, int(task_id_str))
    if ok:
        await message.answer(
            f"🔄 Задача <b>#{task_id_str}</b> — <b>В ПРОЦЕССЕ</b>", parse_mode="HTML"
        )
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
    task_id = int(task_id_str)
    async with AsyncSessionFactory() as session:
        task = await session.get(Task, task_id)
        if not task or task.user_id != message.from_user.id:
            await message.answer(f"Задача #{task_id} не найдена.")
            return
        created = task.created_at
        title_preview = task.title[:40]

    from datetime import timedelta
    local_created = created + timedelta(hours=settings.tz_offset)
    date_label = local_created.strftime("%d.%m")

    await message.answer(
        f"🗑 Удалить задачу?\n"
        f"<b>#{task_id}</b> {title_preview}\n"
        f"<i>Создана: {date_label}</i>",
        reply_markup=_confirm_kb("deltask", task_id, date_label),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("confirm_deltask:"))
async def cb_confirm_deltask(callback: CallbackQuery) -> None:
    task_id = int(callback.data.split(":")[1])
    async with AsyncSessionFactory() as session:
        ok = await delete_task(session, callback.from_user.id, task_id)
    if ok:
        await callback.message.edit_text(f"🗑 Задача #{task_id} удалена.")
    else:
        await callback.message.edit_text(f"Задача #{task_id} не найдена.")
    await callback.answer()


# ── /stats ────────────────────────────────────────────────────────────────────

@router.message(Command("stats"))
async def cmd_stats(message: Message, command: CommandObject) -> None:
    raw = (command.args or "30").strip()
    period = int(raw) if raw.isdigit() and int(raw) in (7, 30, 90) else 30
    async with AsyncSessionFactory() as session:
        stats = await get_user_stats(session, message.from_user.id, period)
    await message.answer(format_stats(stats), parse_mode="HTML")


# ── /search ───────────────────────────────────────────────────────────────────

@router.message(Command("search"))
async def cmd_search(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer("Укажи запрос: /search <b>слово</b>", parse_mode="HTML")
        return

    async with AsyncSessionFactory() as session:
        tasks = await search_tasks(session, message.from_user.id, query)
        memories = await search_memories(session, message.from_user.id, query)
        goals = await search_goals(session, message.from_user.id, query)

    lines = [f"🔍 <b>Поиск: «{query}»</b>\n"]

    if tasks:
        lines.append(f"<b>📋 Задачи ({len(tasks)}):</b>")
        for t in tasks:
            icon = {"done": "✅", "in_progress": "🔄", "todo": "⬜"}.get(t.status, "⬜")
            lines.append(f"  {icon} <b>#{t.id}</b> {t.title}")
        lines.append("")

    if memories:
        lines.append(f"<b>🧠 Память ({len(memories)}):</b>")
        for m in memories:
            lines.append(f"  • <b>{m.key}</b>: {m.value}")
        lines.append("")

    if goals:
        lines.append(f"<b>🎯 Цели ({len(goals)}):</b>")
        for g in goals:
            icon = "✅" if g.status == "done" else "🎯"
            lines.append(f"  {icon} <b>#{g.id}</b> {g.title}")
        lines.append("")

    if not tasks and not memories and not goals:
        lines.append("Ничего не найдено.")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ── /export ───────────────────────────────────────────────────────────────────

@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    await message.answer("📦 Собираю данные...")
    async with AsyncSessionFactory() as session:
        text = await export_user_data(session, message.from_user.id)

    file = BufferedInputFile(
        text.encode("utf-8"),
        filename="asylkhan_export.txt",
    )
    await message.answer_document(file, caption="📦 Твои данные")


# ── Goals ─────────────────────────────────────────────────────────────────────

@router.message(Command("addgoal"))
async def cmd_addgoal(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "Формат: /addgoal <b>название</b> [| дедлайн]\n"
            "Пример: /addgoal Выучить Python | 31.08",
            parse_mode="HTML",
        )
        return

    title = args
    deadline_utc = None
    if "|" in args:
        title_part, _, dl_str = args.partition("|")
        title = title_part.strip()
        try:
            deadline_utc = parse_deadline(dl_str.strip())
        except ValueError as e:
            await message.answer(str(e), parse_mode="HTML")
            return

    if not title:
        await message.answer("Название цели не может быть пустым.")
        return

    async with AsyncSessionFactory() as session:
        goal = await add_goal(session, message.from_user.id, title[:512], deadline=deadline_utc)

    dl_str = f"\n📅 Дедлайн: {format_remind_at(deadline_utc)}" if deadline_utc else ""
    await message.answer(
        f"🎯 Цель добавлена: <b>#{goal.id}</b> {goal.title}{dl_str}",
        parse_mode="HTML",
    )


@router.message(Command("goals"))
async def cmd_goals(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        goals = await get_goals(session, message.from_user.id)
    await message.answer(format_goals_list(goals), parse_mode="HTML")


@router.message(Command("goalset"))
async def cmd_goalset(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        await message.answer(
            "Формат: /goalset <b>id</b> <b>прогресс (0-100)</b>", parse_mode="HTML"
        )
        return
    goal_id, progress = int(parts[0]), int(parts[1])
    async with AsyncSessionFactory() as session:
        ok = await update_goal_progress(session, message.from_user.id, goal_id, progress)
    if ok:
        bar_fill = round(progress / 10)
        bar = "█" * bar_fill + "░" * (10 - bar_fill)
        await message.answer(
            f"🎯 Цель <b>#{goal_id}</b>: [{bar}] {progress}%", parse_mode="HTML"
        )
    else:
        await message.answer(f"Цель #{goal_id} не найдена.")


@router.message(Command("goaldone"))
async def cmd_goaldone(message: Message, command: CommandObject) -> None:
    gid_str = (command.args or "").strip()
    if not gid_str.isdigit():
        await message.answer("Укажи номер: /goaldone <b>id</b>", parse_mode="HTML")
        return
    async with AsyncSessionFactory() as session:
        ok = await complete_goal(session, message.from_user.id, int(gid_str))
    if ok:
        await message.answer(f"🏆 Цель #{gid_str} достигнута! Отличная работа!")
    else:
        await message.answer(f"Цель #{gid_str} не найдена.")


@router.message(Command("delgoal"))
async def cmd_delgoal(message: Message, command: CommandObject) -> None:
    gid_str = (command.args or "").strip()
    if not gid_str.isdigit():
        await message.answer("Укажи номер: /delgoal <b>id</b>", parse_mode="HTML")
        return
    gid = int(gid_str)
    async with AsyncSessionFactory() as session:
        goal = await session.get(Goal, gid)
        if not goal or goal.user_id != message.from_user.id:
            await message.answer(f"Цель #{gid} не найдена.")
            return
        from datetime import timedelta
        local_created = goal.created_at + timedelta(hours=settings.tz_offset)
        date_label = local_created.strftime("%d.%m")
        title_preview = goal.title[:40]

    await message.answer(
        f"🗑 Удалить цель?\n<b>#{gid}</b> {title_preview}\n"
        f"<i>Создана: {date_label}</i>",
        reply_markup=_confirm_kb("delgoal", gid, date_label),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("confirm_delgoal:"))
async def cb_confirm_delgoal(callback: CallbackQuery) -> None:
    gid = int(callback.data.split(":")[1])
    async with AsyncSessionFactory() as session:
        ok = await delete_goal(session, callback.from_user.id, gid)
    if ok:
        await callback.message.edit_text(f"🗑 Цель #{gid} удалена.")
    else:
        await callback.message.edit_text(f"Цель #{gid} не найдена.")
    await callback.answer()


# ── Habits ────────────────────────────────────────────────────────────────────

@router.message(Command("addhabit"))
async def cmd_addhabit(message: Message, command: CommandObject) -> None:
    title = (command.args or "").strip()
    if not title:
        await message.answer(
            "Укажи название: /addhabit <b>зарядка</b>", parse_mode="HTML"
        )
        return
    async with AsyncSessionFactory() as session:
        habit = await add_habit(session, message.from_user.id, title[:256])
    await message.answer(
        f"🔁 Привычка добавлена: <b>#{habit.id}</b> {habit.title}\n"
        "Каждый день отмечай выполнение: /habitdone " + str(habit.id),
        parse_mode="HTML",
    )


@router.message(Command("habits"))
async def cmd_habits(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        habits = await get_habits(session, message.from_user.id)
        done_today = await get_habits_done_today(session, message.from_user.id)

    if not habits:
        await message.answer("🔁 Привычек пока нет. Добавь: /addhabit название")
        return

    lines = ["🔁 <b>Твои привычки:</b>\n"]
    for h in habits:
        streak, best = 0, 0
        async with AsyncSessionFactory() as session2:
            streak, best = await get_habit_streak(session2, h.id)
        done_mark = "✅" if h.id in done_today else "◦"
        lines.append(
            f"{done_mark} <b>#{h.id}</b> {h.title}\n"
            f"   🔥 {streak} дней подряд  |  Рекорд: {best}"
        )
    await message.answer("\n\n".join(lines), parse_mode="HTML")


@router.message(Command("habitdone"))
async def cmd_habitdone(message: Message, command: CommandObject) -> None:
    hid_str = (command.args or "").strip()
    if not hid_str.isdigit():
        await message.answer("Укажи номер: /habitdone <b>id</b>", parse_mode="HTML")
        return
    async with AsyncSessionFactory() as session:
        ok = await log_habit(session, message.from_user.id, int(hid_str))
    if ok:
        # Get new streak
        async with AsyncSessionFactory() as session2:
            streak, best = await get_habit_streak(session2, int(hid_str))
        emoji = "🔥" if streak >= 7 else "✅"
        streak_msg = f" 🔥 Серия: {streak} дней!" if streak > 1 else ""
        await message.answer(f"{emoji} Привычка #{hid_str} отмечена!{streak_msg}")
    else:
        await message.answer(
            f"Привычка #{hid_str} не найдена или уже отмечена сегодня."
        )


@router.message(Command("delhabit"))
async def cmd_delhabit(message: Message, command: CommandObject) -> None:
    hid_str = (command.args or "").strip()
    if not hid_str.isdigit():
        await message.answer("Укажи номер: /delhabit <b>id</b>", parse_mode="HTML")
        return
    hid = int(hid_str)
    async with AsyncSessionFactory() as session:
        habit = await session.get(Habit, hid)
        if not habit or habit.user_id != message.from_user.id:
            await message.answer(f"Привычка #{hid} не найдена.")
            return
        from datetime import timedelta
        local_created = habit.created_at + timedelta(hours=settings.tz_offset)
        date_label = local_created.strftime("%d.%m")
        title_preview = habit.title[:40]

    await message.answer(
        f"🗑 Удалить привычку?\n<b>#{hid}</b> {title_preview}\n"
        f"<i>Добавлена: {date_label}</i>",
        reply_markup=_confirm_kb("delhabit", hid, date_label),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("confirm_delhabit:"))
async def cb_confirm_delhabit(callback: CallbackQuery) -> None:
    hid = int(callback.data.split(":")[1])
    async with AsyncSessionFactory() as session:
        ok = await delete_habit(session, callback.from_user.id, hid)
    if ok:
        await callback.message.edit_text(f"🗑 Привычка #{hid} удалена.")
    else:
        await callback.message.edit_text(f"Привычка #{hid} не найдена.")
    await callback.answer()


# ── Cancel callback ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "cancel_action")
async def cb_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("❌ Отменено.")
    await callback.answer()


# ── /think ────────────────────────────────────────────────────────────────────

@router.message(Command("think"))
async def cmd_think(message: Message) -> None:
    uid = message.from_user.id
    if uid in _think_mode:
        _think_mode.discard(uid)
        start_time = _think_session_start.pop(uid, None)

        # Try to summarize conclusions from the think session
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
            "Говори что думаешь — я буду задавать вопросы, "
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

        # Parse and save conclusions to memory
        saved_count = 0
        from datetime import datetime, timezone
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


# ── /remind ───────────────────────────────────────────────────────────────────

@router.message(Command("remind"))
async def cmd_remind(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "Примеры:\n"
            "<code>/remind через 2 часа позвонить врачу</code>\n"
            "<code>/remind в 15:30 отправить отчёт</code>\n"
            "<code>/remind завтра в 9 встреча</code>",
            parse_mode="HTML",
        )
        return
    try:
        remind_at_utc, text = parse_reminder(args)
    except ValueError as e:
        await message.answer(str(e), parse_mode="HTML")
        return

    async with AsyncSessionFactory() as session:
        reminder = Reminder(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            text=text,
            remind_at=remind_at_utc,
        )
        session.add(reminder)
        await session.commit()
        await session.refresh(reminder)

    when = format_remind_at(remind_at_utc)
    await message.answer(
        f"⏰ Напомню <b>{when}</b>:\n{text}",
        parse_mode="HTML",
    )


@router.message(Command("repeatremind"))
async def cmd_repeatremind(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "Примеры:\n"
            "<code>/repeatremind каждый день в 8:00 зарядка</code>\n"
            "<code>/repeatremind каждую неделю в 9:00 встреча</code>\n"
            "<code>/repeatremind каждый будний день в 8:30 кофе</code>\n"
            "<code>/repeatremind каждый месяц в 10:00 оплатить счёт</code>",
            parse_mode="HTML",
        )
        return
    try:
        pattern, first_utc, text = parse_recurring(args)
    except ValueError as e:
        await message.answer(str(e), parse_mode="HTML")
        return

    pattern_labels = {
        "daily": "каждый день",
        "weekly": "каждую неделю",
        "weekdays": "каждый будний день",
        "monthly": "каждый месяц",
    }

    async with AsyncSessionFactory() as session:
        reminder = Reminder(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            text=text,
            remind_at=first_utc,
            repeat_pattern=pattern,
        )
        session.add(reminder)
        await session.commit()

    when = format_remind_at(first_utc)
    pattern_label = pattern_labels.get(pattern, pattern)
    await message.answer(
        f"🔁 Повторяющееся напоминание создано!\n"
        f"Первый раз: <b>{when}</b>\n"
        f"Повтор: <b>{pattern_label}</b>\n"
        f"Текст: {text}",
        parse_mode="HTML",
    )


@router.message(Command("reminders"))
async def cmd_reminders(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(Reminder)
            .where(Reminder.user_id == message.from_user.id, Reminder.is_sent == False)  # noqa: E712
            .order_by(Reminder.remind_at)
        )
        items = result.scalars().all()

    if not items:
        await message.answer("Активных напоминаний нет.")
        return

    lines = ["⏰ <b>Твои напоминания:</b>\n"]
    for r in items:
        when = format_remind_at(r.remind_at)
        repeat_str = (
            f"  🔁 {r.repeat_pattern}" if r.repeat_pattern else ""
        )
        lines.append(f"<b>#{r.id}</b> — {when}{repeat_str}\n   {r.text}")
    await message.answer("\n\n".join(lines), parse_mode="HTML")


@router.message(Command("delremind"))
async def cmd_delremind(message: Message, command: CommandObject) -> None:
    rid_str = (command.args or "").strip()
    if not rid_str.isdigit():
        await message.answer("Укажи номер: /delremind <b>id</b>", parse_mode="HTML")
        return
    async with AsyncSessionFactory() as session:
        r = await session.get(Reminder, int(rid_str))
        if r and r.user_id == message.from_user.id:
            await session.delete(r)
            await session.commit()
            await message.answer(f"🗑 Напоминание #{rid_str} удалено.")
        else:
            await message.answer(f"Напоминание #{rid_str} не найдено.")


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
    wl_add(uid)  # update in-memory cache immediately
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
        await message.answer(
            "Укажи Telegram ID: /removeuser <b>12345678</b>", parse_mode="HTML"
        )
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
            wl_remove(uid)  # update in-memory cache immediately
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
