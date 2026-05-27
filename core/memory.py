"""
Long-term memory manager for Мейір bot.

Saves/retrieves key-value facts about the user from the DB.
Also provides short-term conversation history (last N messages).
"""

import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.models import MeiMemory, MeiMessage, MeiUser
from config import settings

logger = logging.getLogger(__name__)


# ── User helpers ─────────────────────────────────────────────────────────────

async def get_or_create_user(
    session: AsyncSession,
    user_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
) -> MeiUser:
    user = await session.get(MeiUser, user_id)
    if user is None:
        user = MeiUser(
            id=user_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            is_whitelisted=user_id in settings.allowed_user_ids,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        logger.info("New user registered: %d (%s)", user_id, username)
    else:
        # Update display info silently
        user.username = username
        user.first_name = first_name
        user.last_name = last_name
        await session.commit()
    return user


# ── Conversation history ──────────────────────────────────────────────────────

async def add_message(
    session: AsyncSession,
    user_id: int,
    role: str,
    content: str,
) -> None:
    session.add(MeiMessage(user_id=user_id, role=role, content=content))
    await session.commit()


async def get_history(
    session: AsyncSession,
    user_id: int,
    limit: int = settings.max_history_messages,
) -> List[dict]:
    result = await session.execute(
        select(MeiMessage)
        .where(MeiMessage.user_id == user_id)
        .order_by(MeiMessage.created_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return [{"role": m.role, "content": m.content} for m in reversed(rows)]


async def clear_history(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        delete(MeiMessage).where(MeiMessage.user_id == user_id)
    )
    await session.commit()
    return result.rowcount


# ── Long-term memory ──────────────────────────────────────────────────────────

async def save_memory(
    session: AsyncSession,
    user_id: int,
    key: str,
    value: str,
) -> None:
    """Upsert a memory fact (INSERT … ON CONFLICT DO UPDATE).
    Keys are normalized to lowercase to prevent duplicate entries.
    """
    key = key.strip().lower()  # normalize: "Учёба" → "учёба"
    stmt = (
        pg_insert(MeiMemory)
        .values(user_id=user_id, key=key, value=value)
        .on_conflict_do_update(
            index_elements=["user_id", "key"],
            set_={"value": value, "updated_at": datetime.now(timezone.utc)},
        )
    )
    await session.execute(stmt)
    await session.commit()


async def get_memories(
    session: AsyncSession,
    user_id: int,
) -> List[MeiMemory]:
    result = await session.execute(
        select(MeiMemory)
        .where(MeiMemory.user_id == user_id)
        .order_by(MeiMemory.updated_at.desc())
        .limit(settings.max_memory_items)
    )
    return result.scalars().all()


async def delete_memory(
    session: AsyncSession,
    user_id: int,
    key: str,
) -> bool:
    result = await session.execute(
        delete(MeiMemory).where(MeiMemory.user_id == user_id, MeiMemory.key == key)
    )
    await session.commit()
    return result.rowcount > 0


async def format_memory_context(session: AsyncSession, user_id: int) -> str:
    memories = await get_memories(session, user_id)
    if not memories:
        return "Нет сохранённых данных о пользователе."
    lines = [f"• {m.key}: {m.value}" for m in memories]
    return "\n".join(lines)


# ── Memory extraction ─────────────────────────────────────────────────────────

_EXTRACT_SYSTEM = """\
Ты — система извлечения личных фактов из сообщений пользователя.
Сообщение было отправлено: {msg_date}

Задача: найди ТОЛЬКО постоянные, долгосрочные личные факты, которые верны независимо от текущей даты.

ЗАПОМИНАТЬ можно:
- Имя, возраст, дата рождения, город/страна
- Учёба: вуз, специальность, номер группы, курс
- Работа / профессия
- Дипломный / научный проект (тема, название)
- Интересы, хобби, увлечения
- Семья: партнёр, родители, дети (имена, отношения)
- Долгосрочные жизненные цели (не конкретные задачи)
- Психологические особенности: тревожность, стресс-факторы, триггеры

КАТЕГОРИЧЕСКИ НЕ ЗАПОМИНАТЬ:
- Всё, что содержит "завтра", "сегодня", "вчера", "на этой неделе", "в эту пятницу" и т.п. \
  — это привязано к конкретной дате и уже может быть неактуально
- Расписание конкретного дня
- Что пользователь поел, приготовил, постирал
- Временные события (проверка, встреча, экзамен с конкретной датой)

Ответь СТРОГО в формате (по одному факту на строку):
ключ = значение

Правила:
- Ключ: 1-3 слова, строчными буквами (например: "учёба", "специальность", "хобби")
- Значение: конкретное, без расплывчатости
- Только факты прямо из сообщения — никаких домыслов
- Не дублируй уже известное (список ниже)
- Если значение неизвестно или не упомянуто — НЕ пиши эту строку
- ЗАПРЕЩЕНО: "нет информации", "неизвестно", "не указано", "не упомянуто" как значения
- Если постоянных фактов нет — ответь пустой строкой

Уже известно о пользователе:
{known}
"""

# Fast regex for very short messages (no LLM call needed)
_QUICK_PATTERNS = [
    (r"меня зовут\s+([А-ЯЁа-яёA-Za-z\-]+)", "имя"),
    (r"моё имя\s+([А-ЯЁа-яёA-Za-z\-]+)", "имя"),
    (r"мне\s+(\d{1,3})\s+лет", "возраст"),
]


# Values that are explicitly forbidden — garbage the LLM sometimes outputs
_JUNK_VALUES = frozenset({
    "нет информации", "нет", "неизвестно", "не указано", "не упомянуто",
    "не известно", "отсутствует", "unknown", "n/a", "none", "-", "—",
})

# Words that signal a one-time/temporary event → skip the whole message
_TEMPORAL_SIGNALS = (
    "завтра", "сегодня", "вчера", "послезавтра",
    "на этой неделе", "на прошлой неделе", "на следующей неделе",
    "в эту пятницу", "в эту субботу", "в эту среду",
    "в ближайшие дни", "на этой паре",
)


async def extract_and_save_memories(
    session: AsyncSession,
    user_id: int,
    user_message: str,
    assistant_reply: str,
    message_date: Optional[datetime] = None,
) -> None:
    """
    Three-level extraction:
    1. Explicit triggers  — "запомни: ключ = значение"
    2. Quick regex        — very short messages with obvious patterns
    3. LLM extraction     — smart extraction for any message ≥ 30 chars

    message_date: when the message was sent (used so the LLM can judge
    whether facts are still relevant).
    """
    text = user_message.strip()
    if not text:
        return

    lower = text.lower()

    # ── Level 1: explicit triggers ────────────────────────────────────────────
    for trigger in ("запомни:", "remember:", "запомни,", "сохрани:"):
        if trigger in lower:
            rest = text[lower.index(trigger) + len(trigger):].strip()
            if "=" in rest:
                key, _, value = rest.partition("=")
                key, value = key.strip()[:128], value.strip()[:1000]
                if key and value:
                    await save_memory(session, user_id, key, value)
                    logger.info("Explicit memory: user=%d key=%s", user_id, key)
            return  # explicit trigger found — stop here

    # ── Level 2: quick regex for very short messages ──────────────────────────
    if len(text) < 30:
        for pattern, key in _QUICK_PATTERNS:
            m = re.search(pattern, text, re.I | re.U)
            if m:
                value = m.group(1).strip()[:200]
                if value:
                    await save_memory(session, user_id, key, value)
                    logger.info("Quick regex memory: user=%d %s=%s", user_id, key, value)
        return

    # ── Level 3: LLM-based extraction ────────────────────────────────────────
    # Fast pre-filter: skip messages that are entirely about temporary events.
    is_purely_temporal = any(sig in lower for sig in _TEMPORAL_SIGNALS) and not any(
        sig in lower for sig in (
            "учусь", "работаю", "зовут", "диплом", "проект", "специальн",
            "факульт", "универ", "институт", "хобби", "увлека", "интерес",
        )
    )
    if is_purely_temporal:
        return

    # Skip messages that are unlikely to contain personal facts at all
    personal_signals = (
        "я ", "мне ", "мой ", "моя ", "мои ", "у меня", "себя",
        "учусь", "работаю", "живу", "зовут", "пишу ", "делаю ",
        "увлека", "интерес", "хобби", "диплом", "проект", "специальн",
        "факульт", "универ", "институт", "курс", "тревог", "стресс",
        "боюсь", "люблю", "ненавижу",
    )
    has_personal = any(sig in lower for sig in personal_signals)
    if not has_personal:
        return

    # Format message date for the LLM (helps judge "завтра/сегодня" relevance)
    if message_date:
        if message_date.tzinfo is None:
            message_date = message_date.replace(tzinfo=timezone.utc)
        # Convert to Kazakhstan time (UTC+5)
        from datetime import timedelta
        kz_offset = timedelta(hours=5)
        local_dt = message_date + kz_offset
        msg_date_str = local_dt.strftime("%d %B %Y, %H:%M (UTC+5)")
    else:
        msg_date_str = "дата неизвестна"

    known = await format_memory_context(session, user_id)
    system_prompt = _EXTRACT_SYSTEM.format(known=known, msg_date=msg_date_str)

    try:
        from core.llm import groq_router  # late import to avoid circular
        raw = await groq_router.summarize(
            [{"role": "user", "content": text}],
            system_prompt,
        )
        saved = 0
        for line in raw.strip().splitlines():
            line = line.strip()
            if "=" not in line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            key = key.strip()[:128].lower()
            value = value.strip()[:500]

            # Hard filter: skip garbage values
            if not key or not value or len(key) < 2 or len(value) < 2:
                continue
            if value.lower() in _JUNK_VALUES:
                logger.debug("Skipped junk value: user=%d %s = %r", user_id, key, value)
                continue

            await save_memory(session, user_id, key, value)
            saved += 1
            logger.info("LLM memory: user=%d %s = %s", user_id, key, value[:60])
        if saved:
            logger.info("LLM extracted %d facts for user %d", saved, user_id)
    except Exception as e:
        logger.error("LLM memory extraction failed for user %d: %s", user_id, e)
