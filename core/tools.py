import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Set, Tuple

from sqlalchemy import select, update, or_
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import DeadlineAlert, Goal, Habit, HabitLog, Memory, Reminder, Task

logger = logging.getLogger(__name__)

TODO = "todo"
IN_PROGRESS = "in_progress"
DONE = "done"

PRIORITY_ICON = {"high": "🔴 ", "medium": "", "low": "⚪ "}
STATUS_ICON = {DONE: "✅", IN_PROGRESS: "🔄", TODO: "⬜"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _local_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=settings.tz_offset)


def _local_today() -> date:
    return _local_now().date()


def _format_deadline_tag(deadline_utc: datetime) -> str:
    """Return a short deadline label with urgency icon."""
    now_utc = datetime.now(timezone.utc)
    hours_left = (deadline_utc - now_utc).total_seconds() / 3600
    local_dl = deadline_utc + timedelta(hours=settings.tz_offset)

    if hours_left < 0:
        return f"‼️ ПРОСРОЧЕНО {local_dl.strftime('%d.%m')}"
    elif hours_left < 24:
        return f"🔥 сегодня {local_dl.strftime('%H:%M')}"
    elif hours_left < 48:
        return f"⚠️ завтра {local_dl.strftime('%H:%M')}"
    elif hours_left < 72:
        return f"⚡ {local_dl.strftime('%d.%m')}"
    else:
        return f"📅 {local_dl.strftime('%d.%m.%Y')}"


def _task_line(task: Task) -> str:
    priority = PRIORITY_ICON.get(task.priority, "")
    desc = f"\n   ┗ <i>{task.description}</i>" if task.description else ""
    deadline = (
        f"\n   ⏰ {_format_deadline_tag(task.deadline)}" if task.deadline else ""
    )
    return f"{priority}<b>#{task.id}</b> {task.title}{desc}{deadline}"


# ── Task CRUD ─────────────────────────────────────────────────────────────────

async def add_task(
    session: AsyncSession,
    user_id: int,
    title: str,
    description: Optional[str] = None,
    priority: str = "medium",
    deadline: Optional[datetime] = None,
) -> Task:
    task = Task(
        user_id=user_id,
        title=title,
        description=description,
        status=TODO,
        priority=priority,
        deadline=deadline,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


async def get_tasks(
    session: AsyncSession,
    user_id: int,
    only_pending: bool = True,
) -> List[Task]:
    q = select(Task).where(Task.user_id == user_id)
    if only_pending:
        q = q.where(Task.status != DONE)
    q = q.order_by(Task.created_at.asc())
    result = await session.execute(q)
    return result.scalars().all()


async def get_all_tasks(session: AsyncSession, user_id: int) -> List[Task]:
    result = await session.execute(
        select(Task).where(Task.user_id == user_id).order_by(Task.created_at.asc())
    )
    return result.scalars().all()


async def set_in_progress(session: AsyncSession, user_id: int, task_id: int) -> bool:
    result = await session.execute(
        update(Task)
        .where(Task.id == task_id, Task.user_id == user_id)
        .values(status=IN_PROGRESS, is_done=False)
    )
    await session.commit()
    return result.rowcount > 0


async def complete_task(session: AsyncSession, user_id: int, task_id: int) -> bool:
    result = await session.execute(
        update(Task)
        .where(Task.id == task_id, Task.user_id == user_id)
        .values(status=DONE, is_done=True)
    )
    await session.commit()
    return result.rowcount > 0


async def delete_task(session: AsyncSession, user_id: int, task_id: int) -> bool:
    task = await session.get(Task, task_id)
    if task and task.user_id == user_id:
        await session.delete(task)
        await session.commit()
        return True
    return False


async def search_tasks(
    session: AsyncSession, user_id: int, query: str
) -> List[Task]:
    q_lower = f"%{query.lower()}%"
    result = await session.execute(
        select(Task)
        .where(Task.user_id == user_id)
        .where(Task.title.ilike(q_lower))
        .order_by(Task.created_at.desc())
        .limit(20)
    )
    return result.scalars().all()


async def search_memories(
    session: AsyncSession, user_id: int, query: str
) -> List[Memory]:
    q_lower = f"%{query.lower()}%"
    result = await session.execute(
        select(Memory)
        .where(Memory.user_id == user_id)
        .where(
            or_(Memory.key.ilike(q_lower), Memory.value.ilike(q_lower))
        )
        .limit(20)
    )
    return result.scalars().all()


# ── Habits context for LLM ───────────────────────────────────────────────────

async def get_habits_context(session: AsyncSession, user_id: int) -> str:
    """Return active habits formatted for the LLM system prompt."""
    habits = await get_habits(session, user_id, only_active=True)
    if not habits:
        return ""
    done_today = await get_habits_done_today(session, user_id)
    lines = []
    for h in habits:
        status = "✅ выполнено сегодня" if h.id in done_today else "◦ не отмечено"
        lines.append(f"  #{h.id} {h.title} — {status}")
    return "\n".join(lines)


# ── Task context for LLM ──────────────────────────────────────────────────────

async def get_tasks_context(session: AsyncSession, user_id: int, days: int = 90) -> str:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await session.execute(
        select(Task)
        .where(Task.user_id == user_id, Task.created_at >= since)
        .order_by(Task.created_at.desc())
    )
    tasks = result.scalars().all()
    if not tasks:
        return "Задач за последние 90 дней нет."

    by_date: dict = defaultdict(list)
    for t in tasks:
        dt = t.created_at
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        by_date[dt.date()].append(t)

    lines = []
    for d in sorted(by_date.keys(), reverse=True):
        lines.append(f"{d.strftime('%d.%m.%Y')}:")
        for t in by_date[d]:
            icon = STATUS_ICON.get(t.status, "⬜")
            deadline_str = (
                f" (дедлайн {(t.deadline + timedelta(hours=settings.tz_offset)).strftime('%d.%m')})"
                if t.deadline else ""
            )
            lines.append(f"  {icon} #{t.id} {t.title}{deadline_str}")
    return "\n".join(lines)


# ── Statistics ────────────────────────────────────────────────────────────────

async def get_user_stats(
    session: AsyncSession,
    user_id: int,
    period_days: int = 30,
) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=period_days)
    result = await session.execute(
        select(Task).where(Task.user_id == user_id, Task.created_at >= since)
    )
    tasks = result.scalars().all()

    total = len(tasks)
    done = len([t for t in tasks if t.status == DONE])
    in_progress = len([t for t in tasks if t.status == IN_PROGRESS])
    todo = len([t for t in tasks if t.status == TODO])

    by_priority = {
        "high":   len([t for t in tasks if t.priority == "high"]),
        "medium": len([t for t in tasks if t.priority == "medium"]),
        "low":    len([t for t in tasks if t.priority == "low"]),
    }

    # Deadlines
    overdue = len([
        t for t in tasks
        if t.deadline and t.status != DONE and t.deadline < datetime.now(timezone.utc)
    ])

    return {
        "total": total,
        "done": done,
        "in_progress": in_progress,
        "todo": todo,
        "completion_pct": round(done / total * 100) if total else 0,
        "by_priority": by_priority,
        "overdue": overdue,
        "period_days": period_days,
    }


def format_stats(stats: dict) -> str:
    pct = stats["completion_pct"]
    bar_filled = round(pct / 10)
    bar = "█" * bar_filled + "░" * (10 - bar_filled)
    period = stats["period_days"]
    period_label = {7: "7 дней", 30: "30 дней"}.get(period, f"{period} дней")

    lines = [
        f"📊 <b>Статистика за {period_label}</b>\n",
        f"<b>Выполнено:</b> [{bar}] {pct}%",
        f"Всего: <b>{stats['total']}</b>  ·  ✅ {stats['done']}  ·  🔄 {stats['in_progress']}  ·  📋 {stats['todo']}",
    ]

    if stats["overdue"]:
        lines.append(f"‼️ Просрочено: {stats['overdue']}")

    lines.append("")
    lines.append("<b>По приоритету:</b>")
    lines.append(f"  🔴 Высокий: {stats['by_priority']['high']}")
    lines.append(f"  🟡 Средний: {stats['by_priority']['medium']}")
    lines.append(f"  ⚪ Низкий: {stats['by_priority']['low']}")

    return "\n".join(lines)


# ── Export ────────────────────────────────────────────────────────────────────

async def export_user_data(session: AsyncSession, user_id: int) -> str:
    from core.memory import get_memories  # avoid circular at module level

    now_str = _local_now().strftime("%d.%m.%Y %H:%M")
    lines = [
        "═══════════════════════════════════",
        f"Экспорт данных Асылхан",
        f"Дата: {now_str}",
        "═══════════════════════════════════",
        "",
    ]

    # Memories
    memories = await get_memories(session, user_id)
    lines.append("── ПАМЯТЬ ──────────────────────────")
    if memories:
        for m in memories:
            lines.append(f"{m.key} = {m.value}")
    else:
        lines.append("(пусто)")
    lines.append("")

    # Tasks
    tasks = await get_all_tasks(session, user_id)
    lines.append("── ЗАДАЧИ ──────────────────────────")
    if tasks:
        for t in tasks:
            s_icon = {"done": "✓", "in_progress": "▶", "todo": "○"}.get(t.status, "○")
            p_icon = {"high": "[!] ", "medium": "", "low": "[↓] "}.get(t.priority, "")
            dl_str = ""
            if t.deadline:
                local_dl = t.deadline + timedelta(hours=settings.tz_offset)
                dl_str = f" → до {local_dl.strftime('%d.%m.%Y %H:%M')}"
            lines.append(f"{s_icon} #{t.id} {p_icon}{t.title}{dl_str}")
    else:
        lines.append("(пусто)")
    lines.append("")

    # Goals
    goals = await get_goals(session, user_id)
    lines.append("── ЦЕЛИ ────────────────────────────")
    if goals:
        for g in goals:
            s_icon = {"done": "✓", "active": "○", "archived": "—"}.get(g.status, "○")
            dl_str = ""
            if g.deadline:
                local_dl = g.deadline + timedelta(hours=settings.tz_offset)
                dl_str = f" → до {local_dl.strftime('%d.%m.%Y')}"
            lines.append(f"{s_icon} #{g.id} [{g.progress}%] {g.title}{dl_str}")
            if g.description:
                lines.append(f"   {g.description}")
    else:
        lines.append("(пусто)")
    lines.append("")

    # Habits
    habits = await get_habits(session, user_id)
    lines.append("── ПРИВЫЧКИ ────────────────────────")
    if habits:
        for h in habits:
            streak, best = await get_habit_streak(session, h.id)
            lines.append(f"• #{h.id} {h.title}  (🔥 {streak} дней подряд, лучшее: {best})")
    else:
        lines.append("(пусто)")
    lines.append("")

    # Active reminders
    r_result = await session.execute(
        select(Reminder)
        .where(Reminder.user_id == user_id, Reminder.is_sent == False)  # noqa: E712
        .order_by(Reminder.remind_at)
    )
    reminders = r_result.scalars().all()
    lines.append("── НАПОМИНАНИЯ ─────────────────────")
    if reminders:
        for r in reminders:
            local_time = (r.remind_at + timedelta(hours=settings.tz_offset)).strftime("%d.%m.%Y %H:%M")
            repeat_str = f" [{r.repeat_pattern}]" if r.repeat_pattern else ""
            lines.append(f"⏰ {local_time}{repeat_str}: {r.text}")
    else:
        lines.append("(пусто)")

    return "\n".join(lines)


# ── Goals CRUD ────────────────────────────────────────────────────────────────

async def add_goal(
    session: AsyncSession,
    user_id: int,
    title: str,
    description: Optional[str] = None,
    deadline: Optional[datetime] = None,
) -> Goal:
    goal = Goal(
        user_id=user_id,
        title=title,
        description=description,
        deadline=deadline,
        status="active",
        progress=0,
    )
    session.add(goal)
    await session.commit()
    await session.refresh(goal)
    return goal


async def get_goals(
    session: AsyncSession,
    user_id: int,
    status: Optional[str] = None,
) -> List[Goal]:
    q = select(Goal).where(Goal.user_id == user_id)
    if status:
        q = q.where(Goal.status == status)
    q = q.order_by(Goal.created_at.asc())
    result = await session.execute(q)
    return result.scalars().all()


async def search_goals(
    session: AsyncSession, user_id: int, query: str
) -> List[Goal]:
    q_lower = f"%{query.lower()}%"
    result = await session.execute(
        select(Goal)
        .where(Goal.user_id == user_id)
        .where(
            or_(Goal.title.ilike(q_lower),
                Goal.description.ilike(q_lower))
        )
        .limit(10)
    )
    return result.scalars().all()


async def update_goal_progress(
    session: AsyncSession, user_id: int, goal_id: int, progress: int
) -> bool:
    progress = max(0, min(100, progress))
    result = await session.execute(
        update(Goal)
        .where(Goal.id == goal_id, Goal.user_id == user_id)
        .values(progress=progress)
    )
    await session.commit()
    return result.rowcount > 0


async def complete_goal(session: AsyncSession, user_id: int, goal_id: int) -> bool:
    result = await session.execute(
        update(Goal)
        .where(Goal.id == goal_id, Goal.user_id == user_id)
        .values(status="done", progress=100)
    )
    await session.commit()
    return result.rowcount > 0


async def delete_goal(session: AsyncSession, user_id: int, goal_id: int) -> bool:
    goal = await session.get(Goal, goal_id)
    if goal and goal.user_id == user_id:
        await session.delete(goal)
        await session.commit()
        return True
    return False


def format_goals_list(goals: List[Goal]) -> str:
    if not goals:
        return "🎯 Целей пока нет. Добавь: /addgoal название"

    status_icon = {"active": "🎯", "done": "✅", "archived": "📦"}
    lines = ["🎯 <b>Твои цели:</b>\n"]
    for g in goals:
        icon = status_icon.get(g.status, "🎯")
        bar_fill = round(g.progress / 10)
        bar = "█" * bar_fill + "░" * (10 - bar_fill)
        dl_str = ""
        if g.deadline:
            local_dl = g.deadline + timedelta(hours=settings.tz_offset)
            dl_str = f"  📅 до {local_dl.strftime('%d.%m.%Y')}"
        desc_str = f"\n   <i>{g.description}</i>" if g.description else ""
        lines.append(
            f"{icon} <b>#{g.id}</b> {g.title}{dl_str}\n"
            f"   [{bar}] {g.progress}%{desc_str}"
        )
    return "\n\n".join(lines)


# ── Habits CRUD ───────────────────────────────────────────────────────────────

async def add_habit(
    session: AsyncSession,
    user_id: int,
    title: str,
    frequency: str = "daily",
) -> Habit:
    habit = Habit(user_id=user_id, title=title, frequency=frequency)
    session.add(habit)
    await session.commit()
    await session.refresh(habit)
    return habit


async def get_habits(
    session: AsyncSession,
    user_id: int,
    only_active: bool = True,
) -> List[Habit]:
    q = select(Habit).where(Habit.user_id == user_id)
    if only_active:
        q = q.where(Habit.is_active == True)  # noqa: E712
    q = q.order_by(Habit.created_at.asc())
    result = await session.execute(q)
    return result.scalars().all()


async def log_habit(
    session: AsyncSession, user_id: int, habit_id: int
) -> bool:
    """Log a habit as done today. Returns False if already logged.
    Handles concurrent duplicate inserts gracefully via IntegrityError catch.
    """
    from sqlalchemy.exc import IntegrityError

    habit = await session.get(Habit, habit_id)
    if not habit or habit.user_id != user_id:
        return False

    today = _local_today()
    existing = await session.execute(
        select(HabitLog).where(
            HabitLog.habit_id == habit_id,
            HabitLog.logged_date == today,
        )
    )
    if existing.scalar_one_or_none():
        return False  # already logged today

    try:
        session.add(HabitLog(habit_id=habit_id, user_id=user_id, logged_date=today))
        await session.commit()
        return True
    except IntegrityError:
        # Race condition: another request inserted the same log concurrently
        await session.rollback()
        return False


async def delete_habit(session: AsyncSession, user_id: int, habit_id: int) -> bool:
    habit = await session.get(Habit, habit_id)
    if habit and habit.user_id == user_id:
        await session.delete(habit)
        await session.commit()
        return True
    return False


async def get_habit_streak(
    session: AsyncSession, habit_id: int
) -> Tuple[int, int]:
    """Returns (current_streak, best_streak)."""
    result = await session.execute(
        select(HabitLog.logged_date)
        .where(HabitLog.habit_id == habit_id)
        .order_by(HabitLog.logged_date.desc())
    )
    dates = [row[0] for row in result.all()]
    if not dates:
        return 0, 0

    today = _local_today()
    dates_set = set(dates)

    # Current streak (starting from today or yesterday)
    current = 0
    check = today
    while check in dates_set:
        current += 1
        check -= timedelta(days=1)
    if current == 0:
        check = today - timedelta(days=1)
        while check in dates_set:
            current += 1
            check -= timedelta(days=1)

    # Best streak
    dates_asc = sorted(dates)
    best = 1 if dates_asc else 0
    run = 1
    for i in range(1, len(dates_asc)):
        if (dates_asc[i] - dates_asc[i - 1]).days == 1:
            run += 1
            best = max(best, run)
        else:
            run = 1

    return current, best


async def get_habits_done_today(
    session: AsyncSession, user_id: int
) -> Set[int]:
    today = _local_today()
    result = await session.execute(
        select(HabitLog.habit_id)
        .where(HabitLog.user_id == user_id, HabitLog.logged_date == today)
    )
    return {row[0] for row in result.all()}


def format_habits_list(habits: List[Habit], done_today: Set[int]) -> str:
    if not habits:
        return "🔁 Привычек пока нет. Добавь: /addhabit название"

    lines = ["🔁 <b>Твои привычки:</b>\n"]
    for h in habits:
        done_mark = "✅" if h.id in done_today else "◦"
        lines.append(f"{done_mark} <b>#{h.id}</b> {h.title}   /habitdone {h.id}")
    return "\n".join(lines)


# ── Formatting (plan / task list) ─────────────────────────────────────────────

async def get_tomorrow_tasks(
    session: AsyncSession,
    user_id: int,
) -> List[Task]:
    """Return pending tasks with deadline falling on tomorrow (local time)."""
    tomorrow_local = _local_today() + timedelta(days=1)
    # Convert midnight boundaries to UTC
    from datetime import time as dtime
    tomorrow_start_utc = (
        datetime.combine(tomorrow_local, dtime.min) - timedelta(hours=settings.tz_offset)
    ).replace(tzinfo=timezone.utc)
    tomorrow_end_utc = (
        datetime.combine(tomorrow_local + timedelta(days=1), dtime.min) - timedelta(hours=settings.tz_offset)
    ).replace(tzinfo=timezone.utc)

    result = await session.execute(
        select(Task).where(
            Task.user_id == user_id,
            Task.status != DONE,
            Task.deadline >= tomorrow_start_utc,
            Task.deadline < tomorrow_end_utc,
        ).order_by(Task.deadline.asc())
    )
    return result.scalars().all()


def format_tomorrow_plan(tasks: List[Task]) -> str:
    if not tasks:
        return (
            "📅 <b>ПЛАН НА ЗАВТРА</b>\n\n"
            "<i>Задач на завтра нет.</i>\n\n"
            "Добавь: <i>«завтра мне надо X»</i> или /addtask название|medium|завтра"
        )
    lines = [f"📅 <b>ПЛАН НА ЗАВТРА</b>  <code>[{len(tasks)} задач]</code>\n"]
    for t in tasks:
        lines.append(f"  ◦ {_task_line(t)}")
    return "\n".join(lines)


def format_today_plan(tasks: List[Task]) -> str:
    if not tasks:
        return (
            "📅 <b>ПЛАН НА СЕГОДНЯ</b>\n\n"
            "<i>Задач пока нет. Добавь: /addtask название</i>"
        )

    done = [t for t in tasks if t.status == DONE]
    in_progress = [t for t in tasks if t.status == IN_PROGRESS]
    todo = [t for t in tasks if t.status == TODO]

    total = len(tasks)
    done_count = len(done)

    lines = [f"📅 <b>ПЛАН НА СЕГОДНЯ</b>  <code>[{done_count}/{total}]</code>\n"]

    if done:
        lines.append("✅ <b>ВЫПОЛНЕНО:</b>")
        for t in done:
            lines.append(f"  ✓ <s>{t.title}</s>")
        lines.append("")

    if in_progress:
        lines.append("🔄 <b>В ПРОЦЕССЕ:</b>")
        for t in in_progress:
            lines.append(f"  ▶ {_task_line(t)}")
        lines.append("")

    if todo:
        lines.append("📋 <b>ОСТАЛОСЬ:</b>")
        for t in todo:
            lines.append(f"  ◦ {_task_line(t)}")

    return "\n".join(lines)


def format_tasks_list(tasks: List[Task]) -> str:
    if not tasks:
        return "✅ Список задач пуст!"

    lines = ["📋 <b>Ваши задачи:</b>\n"]
    for t in tasks:
        icon = STATUS_ICON.get(t.status, "⬜")
        desc = f"\n   <i>{t.description}</i>" if t.description else ""
        deadline = f"\n   ⏰ {_format_deadline_tag(t.deadline)}" if t.deadline else ""
        priority = PRIORITY_ICON.get(t.priority, "")
        lines.append(f"{icon} {priority}<b>#{t.id}</b> {t.title}{desc}{deadline}")
    return "\n".join(lines)
