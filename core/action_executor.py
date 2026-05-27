"""
Execute bot actions detected by the LLM from natural-language input.

The LLM embeds a tag at the start of its reply:
  [ACTION:command:params]

This module parses that tag and executes the corresponding action,
returning a result string (or None if the action sends its own message).
"""
import logging
import re
from typing import Optional, Tuple, Union

from core.db import AsyncSessionFactory
from core.reminder_parser import format_remind_at, parse_deadline, parse_reminder
from core.tools import (
    add_goal,
    add_habit,
    add_task,
    complete_task,
    format_stats,
    format_tasks_list,
    format_today_plan,
    get_all_tasks,
    get_habit_streak,
    get_tasks,
    get_user_stats,
    log_habit,
    search_goals,
    search_memories,
    search_tasks,
)
from database.models import Reminder

logger = logging.getLogger(__name__)

# Regex to detect and capture [ACTION:...] at the start of an LLM reply
ACTION_RE = re.compile(r"^\[ACTION:([^\]]+)\]\s*\n?", re.DOTALL)


def extract_action(reply: str) -> Tuple[Optional[str], str]:
    """
    Extract [ACTION:...] tag from the start of an LLM reply.
    Returns (action_content | None, cleaned_reply_text).
    """
    m = ACTION_RE.match(reply.strip())
    if m:
        return m.group(1).strip(), reply[m.end():].strip()
    return None, reply


async def execute_action(
    action_str: str,
    user_id: int,
    chat_id: int,
) -> Union[str, Tuple[str, str], None]:
    """
    Execute an action from LLM intent detection.

    Returns:
      str                 — display as text message
      ("export", text)    — send as file attachment
      None                — action failed silently (already logged)
    """
    # Split "command:params" — params may contain colons (e.g. time formats)
    colon_idx = action_str.find(":")
    if colon_idx == -1:
        command = action_str.lower().strip()
        params = ""
    else:
        command = action_str[:colon_idx].lower().strip()
        params = action_str[colon_idx + 1:].strip()

    try:
        if command == "addtask":
            return await _exec_addtask(user_id, params)
        elif command == "addhabit":
            return await _exec_addhabit(user_id, params)
        elif command == "addgoal":
            return await _exec_addgoal(user_id, params)
        elif command == "remind":
            return await _exec_remind(user_id, chat_id, params)
        elif command == "donetask":
            return await _exec_donetask(user_id, params)
        elif command == "today":
            return await _exec_today(user_id)
        elif command == "tasks":
            return await _exec_tasks(user_id)
        elif command == "stats":
            return await _exec_stats(user_id, params)
        elif command == "export":
            return await _exec_export(user_id)
        elif command == "search":
            return await _exec_search(user_id, params)
        elif command == "habitdone":
            return await _exec_habitdone(user_id, params)
        else:
            logger.warning("Unknown action command: %s", command)
            return None
    except Exception as e:
        logger.error("Action '%s' failed for user %d: %s", command, user_id, e)
        return f"⚠️ Не смог выполнить действие: {e}"


# ── Action handlers ───────────────────────────────────────────────────────────

async def _exec_addtask(user_id: int, params: str) -> str:
    parts = [p.strip() for p in params.split("|")]
    title = parts[0] if parts else ""
    priority = parts[1].strip() if len(parts) > 1 else "medium"
    deadline_str = parts[2].strip() if len(parts) > 2 else ""

    if not title:
        return "⚠️ Не смог добавить задачу — не указано название."

    if priority not in ("high", "medium", "low"):
        priority = "medium"

    deadline_utc = None
    if deadline_str:
        try:
            deadline_utc = parse_deadline(deadline_str)
        except ValueError:
            pass

    async with AsyncSessionFactory() as session:
        task = await add_task(
            session, user_id, title[:512],
            priority=priority, deadline=deadline_utc,
        )

    p_icon = {"high": " 🔴", "medium": "", "low": " ⚪"}.get(priority, "")
    dl_str = f"\n⏰ Дедлайн: {format_remind_at(deadline_utc)}" if deadline_utc else ""
    return f"✅ Задача добавлена: <b>#{task.id}</b> {task.title}{p_icon}{dl_str}"


async def _exec_addhabit(user_id: int, title: str) -> str:
    if not title:
        return "⚠️ Не смог добавить привычку — не указано название."
    async with AsyncSessionFactory() as session:
        habit = await add_habit(session, user_id, title[:256])
    return (
        f"🔁 Привычка добавлена: <b>#{habit.id}</b> {habit.title}\n"
        f"Каждый день отмечай: /habitdone {habit.id}"
    )


async def _exec_addgoal(user_id: int, params: str) -> str:
    parts = [p.strip() for p in params.split("|")]
    title = parts[0] if parts else ""
    deadline_str = parts[1].strip() if len(parts) > 1 else ""

    if not title:
        return "⚠️ Не смог добавить цель — не указано название."

    deadline_utc = None
    if deadline_str:
        try:
            deadline_utc = parse_deadline(deadline_str)
        except ValueError:
            pass

    async with AsyncSessionFactory() as session:
        goal = await add_goal(session, user_id, title[:512], deadline=deadline_utc)

    dl_str = f"\n📅 Дедлайн: {format_remind_at(deadline_utc)}" if deadline_utc else ""
    return f"🎯 Цель добавлена: <b>#{goal.id}</b> {goal.title}{dl_str}"


async def _exec_remind(user_id: int, chat_id: int, params: str) -> str:
    if not params:
        return "⚠️ Не указаны детали напоминания."
    try:
        remind_at_utc, text = parse_reminder(params)
    except ValueError as e:
        return f"⚠️ {e}"

    async with AsyncSessionFactory() as session:
        reminder = Reminder(
            user_id=user_id,
            chat_id=chat_id,
            text=text,
            remind_at=remind_at_utc,
        )
        session.add(reminder)
        await session.commit()

    when = format_remind_at(remind_at_utc)
    return f"⏰ Напомню <b>{when}</b>:\n{text}"


async def _exec_donetask(user_id: int, task_id_str: str) -> str:
    task_id_str = task_id_str.strip()
    if not task_id_str.isdigit():
        return "⚠️ Укажи номер задачи числом."
    async with AsyncSessionFactory() as session:
        ok = await complete_task(session, user_id, int(task_id_str))
    return (
        f"✅ Задача #{task_id_str} выполнена!" if ok
        else f"⚠️ Задача #{task_id_str} не найдена."
    )


async def _exec_today(user_id: int) -> str:
    async with AsyncSessionFactory() as session:
        tasks = await get_all_tasks(session, user_id)
    return format_today_plan(tasks)


async def _exec_tasks(user_id: int) -> str:
    async with AsyncSessionFactory() as session:
        tasks = await get_tasks(session, user_id, only_pending=True)
    return format_tasks_list(tasks)


async def _exec_stats(user_id: int, period_str: str) -> str:
    period_str = period_str.strip()
    period = int(period_str) if period_str.isdigit() else 30
    if period not in (7, 30, 90):
        period = 30
    async with AsyncSessionFactory() as session:
        stats = await get_user_stats(session, user_id, period)
    return format_stats(stats)


async def _exec_export(user_id: int) -> Tuple[str, str]:
    """Returns ("export", text_content) — caller sends as file."""
    from core.tools import export_user_data
    async with AsyncSessionFactory() as session:
        text = await export_user_data(session, user_id)
    return ("export", text)


async def _exec_search(user_id: int, query: str) -> str:
    if not query:
        return "⚠️ Не указан поисковый запрос."

    async with AsyncSessionFactory() as session:
        tasks = await search_tasks(session, user_id, query)
        memories = await search_memories(session, user_id, query)
        goals = await search_goals(session, user_id, query)

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

    return "\n".join(lines)


async def _exec_habitdone(user_id: int, habit_id_str: str) -> str:
    habit_id_str = habit_id_str.strip()
    if not habit_id_str.isdigit():
        return "⚠️ Укажи номер привычки числом."
    hid = int(habit_id_str)
    async with AsyncSessionFactory() as session:
        ok = await log_habit(session, user_id, hid)
    if ok:
        async with AsyncSessionFactory() as session2:
            streak, _ = await get_habit_streak(session2, hid)
        streak_msg = f" 🔥 Серия: {streak} дней!" if streak > 1 else ""
        return f"✅ Привычка #{hid} отмечена!{streak_msg}"
    return "Привычка не найдена или уже отмечена сегодня."
