"""
Parse Russian natural-language time expressions for reminders and deadlines.

One-time reminders (/remind):
  через 30 минут <текст>
  через 2 часа <текст>
  через 3 дня <текст>
  через 1 неделю <текст>
  в 15:30 <текст>
  в 9 <текст>
  завтра в 9 <текст>
  завтра в 15:30 <текст>

Recurring reminders (/repeatremind):
  каждый день в ЧЧ[:ММ] <текст>
  каждую неделю в ЧЧ[:ММ] <текст>
  каждый будний день в ЧЧ[:ММ] <текст>
  каждый месяц в ЧЧ[:ММ] <текст>

Deadlines (/addtask ... | deadline):
  28.05
  28.05.2026
  28.05 18:00
  28.05.2026 18:00
  завтра
  послезавтра
  через 3 дня
  через неделю
"""
import calendar
import re
from datetime import datetime, timedelta, timezone
from typing import Tuple

from config import settings


def _local_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=settings.tz_offset)


def _to_utc(local_dt: datetime) -> datetime:
    return local_dt.replace(tzinfo=None) - timedelta(hours=settings.tz_offset)


# ── One-time reminders ────────────────────────────────────────────────────────

def parse_reminder(args: str) -> Tuple[datetime, str]:
    """
    Returns (remind_at_utc, reminder_text).
    Raises ValueError with user-friendly message on parse failure.
    """
    args = args.strip()
    now = _local_now().replace(second=0, microsecond=0)

    # через X минут/часов/дней/недель <текст>
    m = re.match(
        r"^через\s+(\d+)\s+"
        r"(минут[аыу]?|час[аов]?|дней|дня|день|недел[юьи]?|неделю)\s+(.+)$",
        args, re.I | re.U,
    )
    if m:
        n, unit, text = int(m.group(1)), m.group(2).lower(), m.group(3).strip()
        if unit.startswith("мин"):
            delta = timedelta(minutes=n)
        elif unit.startswith("час"):
            delta = timedelta(hours=n)
        elif unit.startswith("нед"):
            delta = timedelta(weeks=n)
        else:
            delta = timedelta(days=n)
        return _to_utc(now + delta), text

    # завтра в ЧЧ[:ММ] <текст>
    m = re.match(r"^завтра\s+в\s+(\d{1,2})(?::(\d{2}))?\s+(.+)$", args, re.I | re.U)
    if m:
        h, mn, text = int(m.group(1)), int(m.group(2) or 0), m.group(3).strip()
        local_dt = (now + timedelta(days=1)).replace(hour=h, minute=mn, second=0, microsecond=0)
        return _to_utc(local_dt), text

    # в ЧЧ[:ММ] <текст>
    m = re.match(r"^в\s+(\d{1,2})(?::(\d{2}))?\s+(.+)$", args, re.I | re.U)
    if m:
        h, mn, text = int(m.group(1)), int(m.group(2) or 0), m.group(3).strip()
        local_dt = now.replace(hour=h, minute=mn, second=0, microsecond=0)
        if local_dt <= now:
            local_dt += timedelta(days=1)
        return _to_utc(local_dt), text

    raise ValueError(
        "Не понял время. Примеры:\n"
        "<code>/remind через 2 часа позвонить врачу</code>\n"
        "<code>/remind в 15:30 отправить отчёт</code>\n"
        "<code>/remind завтра в 9 встреча с командой</code>\n"
        "<code>/remind через 3 дня оплатить счёт</code>"
    )


# ── Recurring reminders ───────────────────────────────────────────────────────

def parse_recurring(args: str) -> Tuple[str, datetime, str]:
    """
    Parse a recurring reminder specification.
    Returns (pattern, first_occurrence_utc, text).

    pattern: "daily" | "weekly" | "weekdays" | "monthly"
    """
    args = args.strip()
    now = _local_now().replace(second=0, microsecond=0)

    _patterns = [
        (r"^каждый\s+день\s+в\s+(\d{1,2})(?::(\d{2}))?\s+(.+)$", "daily"),
        (r"^каждую\s+неделю\s+в\s+(\d{1,2})(?::(\d{2}))?\s+(.+)$", "weekly"),
        (r"^каждый\s+будний\s+день\s+в\s+(\d{1,2})(?::(\d{2}))?\s+(.+)$", "weekdays"),
        (r"^каждый\s+месяц\s+в\s+(\d{1,2})(?::(\d{2}))?\s+(.+)$", "monthly"),
    ]

    for pattern_re, repeat_type in _patterns:
        m = re.match(pattern_re, args, re.I | re.U)
        if m:
            h = int(m.group(1))
            mn = int(m.group(2) or 0)
            text = m.group(3).strip()

            local_dt = now.replace(hour=h, minute=mn, second=0, microsecond=0)
            if local_dt <= now:
                local_dt += timedelta(days=1)

            # For weekdays, skip to next weekday
            if repeat_type == "weekdays":
                while local_dt.weekday() >= 5:
                    local_dt += timedelta(days=1)

            return repeat_type, _to_utc(local_dt), text

    raise ValueError(
        "Не понял формат. Примеры:\n"
        "<code>/repeatremind каждый день в 8:00 зарядка</code>\n"
        "<code>/repeatremind каждую неделю в 9:00 встреча с командой</code>\n"
        "<code>/repeatremind каждый будний день в 8:30 кофе</code>\n"
        "<code>/repeatremind каждый месяц в 10:00 оплатить счёт</code>"
    )


def next_occurrence(remind_at_utc: datetime, pattern: str) -> datetime:
    """Calculate the next fire time for a recurring reminder."""
    if pattern == "daily":
        return remind_at_utc + timedelta(days=1)
    elif pattern == "weekly":
        return remind_at_utc + timedelta(weeks=1)
    elif pattern == "monthly":
        month = remind_at_utc.month + 1
        year = remind_at_utc.year
        if month > 12:
            month = 1
            year += 1
        try:
            return remind_at_utc.replace(year=year, month=month)
        except ValueError:
            last_day = calendar.monthrange(year, month)[1]
            return remind_at_utc.replace(year=year, month=month, day=last_day)
    elif pattern == "weekdays":
        next_dt = remind_at_utc + timedelta(days=1)
        local_next = next_dt + timedelta(hours=settings.tz_offset)
        while local_next.weekday() >= 5:
            next_dt += timedelta(days=1)
            local_next += timedelta(days=1)
        return next_dt
    # Default: daily
    return remind_at_utc + timedelta(days=1)


# ── Deadline parsing ──────────────────────────────────────────────────────────

def parse_deadline(text: str) -> datetime:
    """
    Parse a deadline string to UTC datetime (naive, UTC).

    Supported formats:
      завтра              → tomorrow 23:59 local
      послезавтра         → day after tomorrow 23:59
      через X дней/недель → X days/weeks from now 23:59
      28.05               → 28 May current year 23:59
      28.05.2026          → 28 May 2026 23:59
      28.05 18:00         → 28 May HH:MM
      28.05.2026 18:00    → explicit
    """
    text = text.strip().lower()
    now = _local_now().replace(second=0, microsecond=0)
    eod = {"hour": 23, "minute": 59, "second": 0, "microsecond": 0}

    if text == "завтра":
        return _to_utc((now + timedelta(days=1)).replace(**eod))

    if text == "послезавтра":
        return _to_utc((now + timedelta(days=2)).replace(**eod))

    # через X дней / через X недель
    m = re.match(
        r"^через\s+(\d+)\s+(день|дня|дней|неделю|недели|недель)$",
        text, re.I,
    )
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = 7 * n if unit.startswith("нед") else n
        return _to_utc((now + timedelta(days=days)).replace(**eod))

    # DD.MM[.YYYY] [HH:MM]
    m = re.match(
        r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\s*(?:(\d{1,2}):(\d{2}))?$",
        text.strip(),
    )
    if m:
        day = int(m.group(1))
        month = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else now.year
        hour = int(m.group(4)) if m.group(4) else 23
        minute = int(m.group(5)) if m.group(5) else 59
        try:
            local_dt = now.replace(
                year=year, month=month, day=day,
                hour=hour, minute=minute, second=0, microsecond=0,
            )
            # If past (or exactly now) and no explicit year — push to next year
            if local_dt <= now and not m.group(3):
                local_dt = local_dt.replace(year=year + 1)
            return _to_utc(local_dt)
        except ValueError:
            pass

    raise ValueError(
        f"Не смог распознать дедлайн: «{text}»\n"
        "Форматы: <code>28.05</code>, <code>28.05 18:00</code>, "
        "<code>завтра</code>, <code>через 3 дня</code>"
    )


# ── Formatting ────────────────────────────────────────────────────────────────

def format_remind_at(utc_dt: datetime, tz_offset: int | None = None) -> str:
    """Format UTC datetime as local time string: DD.MM.YYYY в HH:MM"""
    offset = tz_offset if tz_offset is not None else settings.tz_offset
    local = utc_dt + timedelta(hours=offset)
    return local.strftime("%d.%m.%Y в %H:%M")
