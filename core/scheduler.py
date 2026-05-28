"""
Background scheduler:
  - reminder_loop       — fires pending reminders every 30 s; reschedules recurring ones
  - deadline_loop       — checks task deadlines every 5 min (7d/3d/1d/10h/1h alerts)
  - daily_digest_loop   — sends morning digest at configured hour
  - weekly_report_loop  — sends weekly task summary on configured day/hour
"""
import asyncio
import logging
import random
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import List

from sqlalchemy import select, update

from config import settings
from core.db import AsyncSessionFactory
from core.whitelist import get_allowed_ids
from core.reminder_parser import next_occurrence
from database.models import DeadlineAlert, Habit, HabitLog, Goal, Reminder, Task, User

logger = logging.getLogger(__name__)

_FUN_FACTS = [
    "Бабочки пробуют пищу ногами — их вкусовые рецепторы на лапках!",
    "У осьминога три сердца и голубая кровь.",
    "Один день на Венере длиннее одного года на Венере.",
    "Пчёлы могут распознавать человеческие лица.",
    "Акулы существуют уже более 400 млн лет — они старше деревьев.",
    "Луна удаляется от Земли примерно на 3,8 см каждый год.",
    "Мозг генерирует около 23 Вт — достаточно для тусклой лампочки.",
    "Дельфины спят с одним открытым глазом.",
    "Радуга — это полный круг. Мы видим дугу из-за горизонта.",
    "Молния бьёт в Землю около 100 раз в секунду.",
    "Слоны — единственные животные, которые не умеют прыгать.",
    "Мёд не портится. В египетских пирамидах находили тысячелетний мёд.",
    "Звук путешествует в воде в 4 раза быстрее, чем в воздухе.",
    "Бамбук может расти до 91 см в день — быстрейшее растение на Земле.",
    "Совы не могут двигать глазами — они поворачивают голову до 270°.",
    "Горячая вода замерзает быстрее холодной (эффект Мпемба).",
    "Кошки мяукают только для общения с людьми, не друг с другом.",
    "Гора Олимп на Марсе в 3 раза выше Эвереста.",
    "Осьминоги имеют 9 мозгов: 1 центральный и по одному на каждое щупальце.",
    "В теле человека больше бактерий, чем клеток.",
    "Первая компьютерная программа была написана Адой Лавлейс ещё в 1843 году.",
    "Скорость мысли — около 120 м/с по нейронным путям.",
    "«Dreamt» — единственное слово в английском, оканчивающееся на «-mt».",
    "Человек — единственное животное, которое краснеет.",
]

# Deadline thresholds: (name, timedelta_before_deadline)
_DEADLINE_THRESHOLDS = [
    ("7d",  timedelta(days=7)),
    ("3d",  timedelta(days=3)),
    ("1d",  timedelta(days=1)),
    ("10h", timedelta(hours=10)),
    ("1h",  timedelta(hours=1)),
]


def _local_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=settings.tz_offset)


def _local_today() -> date:
    return _local_now().date()


# ── Reminders ─────────────────────────────────────────────────────────────────

async def reminder_loop(bot) -> None:
    """Fires pending reminders every 30 s. Reschedules recurring ones."""
    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            async with AsyncSessionFactory() as session:
                result = await session.execute(
                    select(Reminder).where(
                        Reminder.remind_at <= now_utc,
                        Reminder.is_sent == False,  # noqa: E712
                    )
                )
                due = result.scalars().all()
                for r in due:
                    sent = False
                    try:
                        await bot.send_message(
                            r.chat_id,
                            f"⏰ <b>Напоминание:</b>\n\n{r.text}",
                            parse_mode="HTML",
                        )
                        sent = True
                        logger.info("Reminder %d sent to chat %d", r.id, r.chat_id)
                    except Exception as e:
                        logger.error("Failed to send reminder %d: %s", r.id, e)

                    if not sent:
                        continue  # Don't mark as sent — retry on next tick

                    # Mark as sent only after successful delivery
                    await session.execute(
                        update(Reminder)
                        .where(Reminder.id == r.id)
                        .values(is_sent=True)
                    )

                    # Reschedule recurring reminder
                    if r.repeat_pattern:
                        next_dt = next_occurrence(r.remind_at, r.repeat_pattern)
                        new_r = Reminder(
                            user_id=r.user_id,
                            chat_id=r.chat_id,
                            text=r.text,
                            remind_at=next_dt,
                            repeat_pattern=r.repeat_pattern,
                        )
                        session.add(new_r)
                        logger.info(
                            "Rescheduled recurring reminder %d → %s", r.id, next_dt
                        )

                await session.commit()
        except Exception as e:
            logger.error("Reminder loop error: %s", e)
        await asyncio.sleep(30)


# ── Deadline alerts ───────────────────────────────────────────────────────────

async def deadline_loop(bot) -> None:
    """Checks upcoming task deadlines every 5 minutes and sends alerts."""
    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            async with AsyncSessionFactory() as session:
                # Get all tasks with deadlines that are not done
                result = await session.execute(
                    select(Task).where(
                        Task.deadline.is_not(None),
                        Task.status != "done",
                    )
                )
                tasks = result.scalars().all()

                for task in tasks:
                    time_left = task.deadline - now_utc

                    for threshold_name, threshold_delta in _DEADLINE_THRESHOLDS:
                        # Check if we're within the threshold window (±5 min tolerance)
                        if abs(time_left.total_seconds() - threshold_delta.total_seconds()) > 300:
                            continue
                        if time_left.total_seconds() > threshold_delta.total_seconds():
                            continue

                        # Check if already sent
                        existing = await session.execute(
                            select(DeadlineAlert).where(
                                DeadlineAlert.task_id == task.id,
                                DeadlineAlert.threshold == threshold_name,
                            )
                        )
                        if existing.scalar_one_or_none():
                            continue  # already notified

                        # Format label
                        labels = {
                            "7d": "7 дней",
                            "3d": "3 дня",
                            "1d": "1 день",
                            "10h": "10 часов",
                            "1h": "1 час",
                        }
                        label = labels.get(threshold_name, threshold_name)
                        local_dl = task.deadline + timedelta(hours=settings.tz_offset)
                        priority_mark = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(
                            task.priority, ""
                        )

                        msg = (
                            f"⏰ <b>Дедлайн через {label}!</b>\n\n"
                            f"{priority_mark} <b>#{task.id}</b> {task.title}\n"
                            f"Срок: {local_dl.strftime('%d.%m.%Y в %H:%M')}"
                        )
                        try:
                            await bot.send_message(task.user_id, msg, parse_mode="HTML")
                            logger.info(
                                "Deadline alert %s sent for task %d", threshold_name, task.id
                            )
                        except Exception as e:
                            logger.error(
                                "Failed to send deadline alert for task %d: %s", task.id, e
                            )

                        # Save alert record
                        session.add(DeadlineAlert(
                            task_id=task.id,
                            threshold=threshold_name,
                        ))

                await session.commit()
        except Exception as e:
            logger.error("Deadline loop error: %s", e)
        await asyncio.sleep(300)  # check every 5 minutes


# ── Daily digest ──────────────────────────────────────────────────────────────

_last_digest_date: str = ""


async def daily_digest_loop(bot) -> None:
    """Sends morning digest at settings.daily_digest_hour local time."""
    global _last_digest_date
    while True:
        try:
            now = _local_now()
            today_str = now.strftime("%Y-%m-%d")
            if (
                now.hour == settings.daily_digest_hour
                and _last_digest_date != today_str
            ):
                _last_digest_date = today_str
                await _send_digests(bot)
        except Exception as e:
            logger.error("Daily digest loop error: %s", e)
        await asyncio.sleep(60)


async def _send_digests(bot) -> None:
    fun_fact = random.choice(_FUN_FACTS)

    async with AsyncSessionFactory() as session:
        for user_id in get_allowed_ids():
            try:
                # Tasks
                tasks_result = await session.execute(
                    select(Task)
                    .where(Task.user_id == user_id)
                    .order_by(Task.created_at.asc())
                )
                tasks = tasks_result.scalars().all()

                # Today's reminders
                today_start = datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                today_end = today_start + timedelta(days=1)
                rem_result = await session.execute(
                    select(Reminder).where(
                        Reminder.user_id == user_id,
                        Reminder.remind_at >= today_start,
                        Reminder.remind_at < today_end,
                        Reminder.is_sent == False,  # noqa: E712
                    ).order_by(Reminder.remind_at)
                )
                reminders_today = rem_result.scalars().all()

                # Goals
                goals_result = await session.execute(
                    select(Goal)
                    .where(Goal.user_id == user_id, Goal.status == "active")
                    .order_by(Goal.created_at.asc())
                )
                active_goals = goals_result.scalars().all()

                # Habits
                habits_result = await session.execute(
                    select(Habit)
                    .where(Habit.user_id == user_id, Habit.is_active == True)  # noqa: E712
                    .order_by(Habit.created_at.asc())
                )
                habits = habits_result.scalars().all()

                today = _local_today()
                habits_done_result = await session.execute(
                    select(HabitLog.habit_id).where(
                        HabitLog.user_id == user_id,
                        HabitLog.logged_date == today,
                    )
                )
                done_habit_ids = {row[0] for row in habits_done_result.all()}

                # User's first name
                user_obj = await session.get(User, user_id)
                user_name = (user_obj.first_name or "") if user_obj else ""

                digest = _build_digest(
                    tasks, reminders_today, active_goals,
                    habits, done_habit_ids, fun_fact, user_name,
                )
                await bot.send_message(user_id, digest, parse_mode="HTML")
                logger.info("Daily digest sent to user %d", user_id)
            except Exception as e:
                logger.error("Failed to send digest to %d: %s", user_id, e)


def _build_digest(
    tasks: list,
    reminders_today: list,
    goals: list,
    habits: list,
    habits_done: set,
    fun_fact: str,
    user_name: str = "",
) -> str:
    now = _local_now()

    # Russian weekday/month names
    weekdays = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    months = ["января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    date_str = f"{weekdays[now.weekday()]}, {now.day} {months[now.month - 1]} {now.year}"

    greeting_name = f", {user_name}" if user_name else ""
    lines = [
        f"☀️ <b>Доброе утро{greeting_name}!</b>",
        f"<i>{date_str}</i>\n",
    ]

    # Tasks section
    pending = [t for t in tasks if t.status != "done"]
    in_progress = [t for t in pending if t.status == "in_progress"]
    todo_tasks = [t for t in pending if t.status == "todo"]

    if not pending:
        lines.append("✅ <b>Все задачи выполнены!</b>\n")
    else:
        lines.append(f"📋 <b>Задач: {len(pending)}</b>")
        for t in in_progress:
            lines.append(f"  🔄 {t.title}")
        # Show up to 5 priority-sorted todo tasks
        high = [t for t in todo_tasks if t.priority == "high"]
        rest = [t for t in todo_tasks if t.priority != "high"]
        shown = (high + rest)[:5]
        for t in shown:
            p = "🔴 " if t.priority == "high" else ""
            dl = ""
            if t.deadline:
                dl = f" — {_tag_short(t.deadline)}"
            lines.append(f"  ◦ {p}{t.title}{dl}")
        if len(todo_tasks) > 5:
            lines.append(f"  <i>...и ещё {len(todo_tasks) - 5}</i>")
        lines.append("")

    # Today's reminders
    if reminders_today:
        lines.append("⏰ <b>Напоминания сегодня:</b>")
        for r in reminders_today:
            t_str = (r.remind_at + timedelta(hours=settings.tz_offset)).strftime("%H:%M")
            lines.append(f"  • {t_str} — {r.text}")
        lines.append("")

    # Habits
    pending_habits = [h for h in habits if h.id not in habits_done]
    if pending_habits:
        lines.append("🔁 <b>Привычки на сегодня:</b>")
        for h in pending_habits:
            lines.append(f"  ◦ {h.title}")
        lines.append("")
    elif habits:
        lines.append("✅ <b>Все привычки уже выполнены!</b>\n")

    # Goals (top 3 active)
    if goals:
        lines.append(f"🎯 <b>Цели ({len(goals)}):</b>")
        for g in sorted(goals, key=lambda x: x.progress)[:3]:
            bar_fill = round(g.progress / 10)
            bar = "█" * bar_fill + "░" * (10 - bar_fill)
            lines.append(f"  [{bar}] {g.progress}% {g.title}")
        lines.append("")

    # Fun fact
    lines.append(f"💡 <b>Факт дня:</b> <i>{fun_fact}</i>")

    return "\n".join(lines)


def _tag_short(deadline_utc: datetime) -> str:
    now_utc = datetime.now(timezone.utc)
    hours_left = (deadline_utc - now_utc).total_seconds() / 3600
    local = deadline_utc + timedelta(hours=settings.tz_offset)
    if hours_left < 0:
        return f"‼️просрочено"
    elif hours_left < 24:
        return f"🔥сегодня {local.strftime('%H:%M')}"
    elif hours_left < 48:
        return f"⚠️завтра"
    else:
        return f"📅{local.strftime('%d.%m')}"


# ── Weekly report ─────────────────────────────────────────────────────────────

_last_report_date: str = ""


async def weekly_report_loop(bot) -> None:
    """Sends weekly report on settings.weekly_report_day at settings.weekly_report_hour."""
    global _last_report_date
    while True:
        try:
            now = _local_now()
            today_str = now.strftime("%Y-%W")
            if (
                now.weekday() == settings.weekly_report_day
                and now.hour == settings.weekly_report_hour
                and _last_report_date != today_str
            ):
                _last_report_date = today_str
                await _send_reports(bot)
        except Exception as e:
            logger.error("Weekly report loop error: %s", e)
        await asyncio.sleep(60)


async def _send_reports(bot) -> None:
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    async with AsyncSessionFactory() as session:
        users_result = await session.execute(
            select(User).where(User.is_active == True)  # noqa: E712
        )
        users = users_result.scalars().all()

        for user in users:
            if user.id not in settings.allowed_user_ids:
                continue
            tasks_result = await session.execute(
                select(Task)
                .where(Task.user_id == user.id, Task.created_at >= week_ago)
                .order_by(Task.created_at)
            )
            tasks = tasks_result.scalars().all()
            report = _build_report(tasks)
            try:
                await bot.send_message(user.id, report, parse_mode="HTML")
                logger.info("Weekly report sent to user %d", user.id)
            except Exception as e:
                logger.error("Failed to send weekly report to %d: %s", user.id, e)


def _build_report(tasks: list) -> str:
    now = _local_now()
    week_start = (now - timedelta(days=7)).strftime("%d.%m")
    week_end = now.strftime("%d.%m.%Y")

    if not tasks:
        return (
            f"📊 <b>Отчёт за неделю</b> ({week_start} — {week_end})\n\n"
            "За эту неделю задач не было."
        )

    done = [t for t in tasks if t.status == "done"]
    in_prog = [t for t in tasks if t.status == "in_progress"]
    todo = [t for t in tasks if t.status == "todo"]

    pct = round(len(done) / len(tasks) * 100) if tasks else 0
    bar_filled = round(pct / 10)
    bar = "█" * bar_filled + "░" * (10 - bar_filled)

    lines = [
        f"📊 <b>Отчёт за неделю</b> ({week_start} — {week_end})\n",
        f"[{bar}] {pct}%",
        f"Всего: {len(tasks)}  ·  ✅ {len(done)}  ·  🔄 {len(in_prog)}  ·  📋 {len(todo)}\n",
    ]

    if done:
        lines.append("✅ <b>Выполнено:</b>")
        for t in done:
            lines.append(f"  • {t.title}")
        lines.append("")

    if in_prog:
        lines.append("🔄 <b>В процессе:</b>")
        for t in in_prog:
            lines.append(f"  • {t.title}")
        lines.append("")

    if todo:
        lines.append("📋 <b>Не сделано:</b>")
        for t in todo:
            lines.append(f"  • {t.title}")

    return "\n".join(lines)
