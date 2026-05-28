"""
Psychology and mood tools for Мейір bot.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import MeiUser, MeiMessage, MeiMemory, MoodLog, JournalEntry

logger = logging.getLogger(__name__)


def _local_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=settings.tz_offset)


# ── Mood logging ──────────────────────────────────────────────────────────────

async def log_mood(
    session: AsyncSession,
    user_id: int,
    score: int,
    notes: Optional[str] = None,
) -> MoodLog:
    """Log user's mood score (1-10) with optional notes."""
    score = max(1, min(10, score))
    entry = MoodLog(user_id=user_id, score=score, notes=notes)
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    logger.info("Mood logged: user=%d score=%d", user_id, score)
    return entry


async def get_mood_history(
    session: AsyncSession,
    user_id: int,
    limit: int = 14,
) -> List[MoodLog]:
    """Get recent mood log entries."""
    result = await session.execute(
        select(MoodLog)
        .where(MoodLog.user_id == user_id)
        .order_by(MoodLog.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


async def get_mood_stats(
    session: AsyncSession,
    user_id: int,
    days: int = 30,
) -> dict:
    """Get mood statistics for the past N days."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await session.execute(
        select(MoodLog)
        .where(MoodLog.user_id == user_id, MoodLog.created_at >= since)
        .order_by(MoodLog.created_at.asc())
    )
    logs = result.scalars().all()

    if not logs:
        return {
            "average": None,
            "min": None,
            "max": None,
            "count": 0,
            "trend": "нет данных",
        }

    scores = [l.score for l in logs]
    avg = round(sum(scores) / len(scores), 1)

    # Calculate trend: compare first half vs second half
    trend = "стабильно"
    if len(scores) >= 4:
        mid = len(scores) // 2
        first_half_avg = sum(scores[:mid]) / mid
        second_half_avg = sum(scores[mid:]) / (len(scores) - mid)
        diff = second_half_avg - first_half_avg
        if diff >= 0.5:
            trend = "улучшается"
        elif diff <= -0.5:
            trend = "ухудшается"

    return {
        "average": avg,
        "min": min(scores),
        "max": max(scores),
        "count": len(scores),
        "trend": trend,
    }


# ── Journal ───────────────────────────────────────────────────────────────────

async def add_journal(
    session: AsyncSession,
    user_id: int,
    content: str,
    mood_score: Optional[int] = None,
    tags: Optional[str] = None,
) -> JournalEntry:
    """Add a journal entry."""
    if mood_score is not None:
        mood_score = max(1, min(10, mood_score))
    entry = JournalEntry(
        user_id=user_id,
        content=content,
        mood_score=mood_score,
        tags=tags,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    logger.info("Journal entry added: user=%d", user_id)
    return entry


async def get_journal(
    session: AsyncSession,
    user_id: int,
    limit: int = 5,
) -> List[JournalEntry]:
    """Get recent journal entries."""
    result = await session.execute(
        select(JournalEntry)
        .where(JournalEntry.user_id == user_id)
        .order_by(JournalEntry.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


# ── Formatting ────────────────────────────────────────────────────────────────

def _mood_bar(score: int) -> str:
    """Generate a visual mood bar like ▓▓▓▓▓░░░░░"""
    filled = score
    empty = 10 - score
    return "▓" * filled + "░" * empty


def format_mood_history(logs: List[MoodLog]) -> str:
    """Format mood history as HTML with visual bars."""
    if not logs:
        return "📊 История настроений пока пуста.\nЗапиши первое настроение: /mood 7"

    lines = ["📊 <b>История настроений:</b>\n"]
    for log in reversed(logs):  # show oldest first
        dt = log.created_at
        if dt.tzinfo is not None:
            local_dt = dt.replace(tzinfo=None) + timedelta(hours=settings.tz_offset)
        else:
            local_dt = dt + timedelta(hours=settings.tz_offset)
        date_str = local_dt.strftime("%d.%m %H:%M")
        bar = _mood_bar(log.score)
        notes_str = f"\n   <i>{log.notes}</i>" if log.notes else ""
        lines.append(f"<code>{date_str}</code> [{bar}] {log.score}/10{notes_str}")

    return "\n".join(lines)


def format_mood_stats(stats: dict) -> str:
    """Format mood statistics as HTML."""
    if stats["count"] == 0:
        return (
            "📈 <b>Статистика настроений</b>\n\n"
            "Данных пока нет. Начни отслеживать настроение: /mood 7"
        )

    avg = stats["average"]
    bar_fill = round(avg)
    bar = _mood_bar(bar_fill)
    trend_emoji = {
        "улучшается": "📈",
        "ухудшается": "📉",
        "стабильно": "➡️",
        "нет данных": "❓",
    }.get(stats["trend"], "➡️")

    lines = [
        "📈 <b>Статистика настроений (30 дней)</b>\n",
        f"Среднее: <b>[{bar}] {avg}/10</b>",
        f"Минимум: {stats['min']}/10  ·  Максимум: {stats['max']}/10",
        f"Записей: {stats['count']}",
        f"Тренд: {trend_emoji} <b>{stats['trend']}</b>",
    ]
    return "\n".join(lines)


def format_journal(entries: List[JournalEntry]) -> str:
    """Format journal entries as HTML."""
    if not entries:
        return (
            "📔 <b>Дневник</b>\n\n"
            "Записей пока нет.\nДобавь первую: /journal Сегодня я понял что..."
        )

    lines = ["📔 <b>Последние записи дневника:</b>\n"]
    for entry in entries:
        dt = entry.created_at
        if dt.tzinfo is not None:
            local_dt = dt.replace(tzinfo=None) + timedelta(hours=settings.tz_offset)
        else:
            local_dt = dt + timedelta(hours=settings.tz_offset)
        date_str = local_dt.strftime("%d.%m.%Y %H:%M")

        mood_str = ""
        if entry.mood_score:
            mood_str = f" · настроение {entry.mood_score}/10"

        tags_str = ""
        if entry.tags:
            tags_str = f"\n   🏷 {entry.tags}"

        content_preview = entry.content[:200] + "…" if len(entry.content) > 200 else entry.content
        lines.append(
            f"<b>{date_str}</b>{mood_str}\n"
            f"{content_preview}{tags_str}"
        )

    return "\n\n".join(lines)


# ── Mood context for LLM ──────────────────────────────────────────────────────

async def get_mood_context(session: AsyncSession, user_id: int) -> str:
    """Generate mood context string for the LLM system prompt."""
    logs = await get_mood_history(session, user_id, limit=7)
    stats = await get_mood_stats(session, user_id, days=30)

    if not logs:
        return "Пользователь ещё не записывал настроение."

    lines = []

    # Recent mood entries
    lines.append("Последние записи настроения:")
    for log in reversed(logs):
        dt = log.created_at
        if dt.tzinfo is not None:
            local_dt = dt.replace(tzinfo=None) + timedelta(hours=settings.tz_offset)
        else:
            local_dt = dt + timedelta(hours=settings.tz_offset)
        date_str = local_dt.strftime("%d.%m")
        notes_str = f" ({log.notes})" if log.notes else ""
        lines.append(f"  {date_str}: {log.score}/10{notes_str}")

    # Stats summary
    if stats["count"] > 0:
        lines.append(f"\nСредний балл за 30 дней: {stats['average']}/10")
        lines.append(f"Тренд: {stats['trend']}")

    return "\n".join(lines)


# ── Memory/history helpers (also exposed for commands) ────────────────────────

async def get_or_create_user(
    session,
    user_id: int,
    username,
    first_name,
    last_name,
) -> MeiUser:
    """Delegate to memory module."""
    from core.memory import get_or_create_user as _get_or_create
    return await _get_or_create(session, user_id, username, first_name, last_name)


async def add_message(session, user_id: int, role: str, content: str) -> None:
    """Delegate to memory module."""
    from core.memory import add_message as _add_message
    await _add_message(session, user_id, role, content)


async def get_history(session, user_id: int, limit: int = 20) -> List[dict]:
    """Delegate to memory module."""
    from core.memory import get_history as _get_history
    return await _get_history(session, user_id, limit)


async def clear_history(session, user_id: int) -> int:
    """Delegate to memory module."""
    from core.memory import clear_history as _clear_history
    return await _clear_history(session, user_id)


# ── Export ────────────────────────────────────────────────────────────────────

async def export_user_data(session: AsyncSession, user_id: int) -> str:
    """Export all user data as plain text."""
    from core.memory import get_memories

    now_str = _local_now().strftime("%d.%m.%Y %H:%M")
    lines = [
        "═══════════════════════════════════",
        f"Экспорт данных Мейір",
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

    # Mood history
    mood_logs = await get_mood_history(session, user_id, limit=100)
    lines.append("── НАСТРОЕНИЕ ──────────────────────")
    if mood_logs:
        for log in reversed(mood_logs):
            dt = log.created_at
            if dt.tzinfo is not None:
                local_dt = dt.astimezone(timezone.utc) + timedelta(hours=settings.tz_offset)
            else:
                local_dt = dt + timedelta(hours=settings.tz_offset)
            date_str = local_dt.strftime("%d.%m.%Y %H:%M")
            notes_str = f" — {log.notes}" if log.notes else ""
            lines.append(f"{date_str}: {log.score}/10{notes_str}")
    else:
        lines.append("(пусто)")
    lines.append("")

    # Journal
    journal_entries = await get_journal(session, user_id, limit=200)
    lines.append("── ДНЕВНИК ─────────────────────────")
    if journal_entries:
        for entry in reversed(journal_entries):
            dt = entry.created_at
            if dt.tzinfo is not None:
                local_dt = dt.astimezone(timezone.utc) + timedelta(hours=settings.tz_offset)
            else:
                local_dt = dt + timedelta(hours=settings.tz_offset)
            date_str = local_dt.strftime("%d.%m.%Y %H:%M")
            mood_str = f" [настроение: {entry.mood_score}/10]" if entry.mood_score else ""
            tags_str = f" [теги: {entry.tags}]" if entry.tags else ""
            lines.append(f"\n{date_str}{mood_str}{tags_str}")
            lines.append(entry.content)
    else:
        lines.append("(пусто)")

    return "\n".join(lines)


# ── Breathing exercises ───────────────────────────────────────────────────────

BREATHING_EXERCISES = {
    "478": {
        "name": "Дыхание 4-7-8",
        "description": "Успокаивающая техника, снижает тревогу и помогает заснуть.",
        "steps": [
            "1️⃣ Сядь удобно, спина прямая",
            "2️⃣ Выдохни полностью через рот",
            "3️⃣ Закрой рот, вдохни через нос на <b>4 счёта</b>",
            "4️⃣ Задержи дыхание на <b>7 счётов</b>",
            "5️⃣ Выдохни через рот на <b>8 счётов</b>",
            "6️⃣ Повтори цикл <b>4 раза</b>",
            "",
            "<i>Совет: делай медленно, не торопись. После каждого цикла становится легче.</i>",
        ],
    },
    "box": {
        "name": "Коробочное дыхание (Box breathing)",
        "description": "Техника, используемая спецназом для снятия стресса.",
        "steps": [
            "1️⃣ Вдохни на <b>4 счёта</b>",
            "2️⃣ Задержи дыхание на <b>4 счёта</b>",
            "3️⃣ Выдохни на <b>4 счёта</b>",
            "4️⃣ Задержи дыхание на <b>4 счёта</b>",
            "5️⃣ Повтори <b>4-6 раз</b>",
            "",
            "<i>Представь квадрат: каждая сторона — один шаг дыхания.</i>",
        ],
    },
    "calm": {
        "name": "Успокаивающее дыхание",
        "description": "Простая техника для быстрого успокоения в любой ситуации.",
        "steps": [
            "1️⃣ Вдохни медленно через нос на <b>5 счётов</b>",
            "2️⃣ Задержи дыхание на <b>2 счёта</b>",
            "3️⃣ Медленно выдохни через рот на <b>7 счётов</b>",
            "4️⃣ Выдох длиннее вдоха — это активирует парасимпатику",
            "5️⃣ Повтори <b>5-10 раз</b>",
            "",
            "<i>Можно делать незаметно в любом месте. Фокусируйся только на дыхании.</i>",
        ],
    },
}


def format_breathing_exercise(technique: str) -> str:
    """Format a breathing exercise as HTML."""
    ex = BREATHING_EXERCISES.get(technique)
    if not ex:
        return "Техника не найдена."

    lines = [
        f"🌬 <b>{ex['name']}</b>\n",
        f"<i>{ex['description']}</i>\n",
    ] + ex["steps"]

    return "\n".join(lines)


# ── Affirmations ──────────────────────────────────────────────────────────────

AFFIRMATIONS = [
    "Я достаточно хорош(а) таким(ой), какой(ая) я есть прямо сейчас.",
    "Каждый день я становлюсь лучшей версией себя.",
    "Я справляюсь с трудностями и выхожу из них сильнее.",
    "Мои чувства важны и имеют право на существование.",
    "Я заслуживаю любви, заботы и уважения.",
    "Я способен(на) справиться со всем, что встречается на моём пути.",
    "Мои ошибки — это уроки, а не приговор.",
    "Я выбираю спокойствие вместо тревоги.",
    "С каждым вдохом я становлюсь спокойнее и увереннее.",
    "Я доверяю процессу своей жизни.",
    "Я достоин(на) счастья и внутреннего покоя.",
    "Мои проблемы временны, а моя сила постоянна.",
    "Я отпускаю то, что не могу контролировать.",
    "Я благодарен(на) за то, что у меня есть прямо сейчас.",
    "Каждый новый день — это новая возможность.",
    "Я принимаю себя со всеми своими несовершенствами.",
    "Моё мнение о себе важнее мнения других.",
    "Я нахожусь именно там, где мне нужно быть.",
    "Я способен(на) создать изменения в своей жизни.",
    "Я выбираю радость и благодарность в этот момент.",
]
