"""
Long-term memory manager.

Saves/retrieves key-value facts about the user from the DB.
Also provides short-term conversation history (last N messages).
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database.models import Memory, Message, User
from config import settings

logger = logging.getLogger(__name__)


# ── User helpers ─────────────────────────────────────────────────────────────

async def get_or_create_user(
    session: AsyncSession,
    user_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        user = User(
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
    session.add(Message(user_id=user_id, role=role, content=content))
    await session.commit()


async def get_history(
    session: AsyncSession,
    user_id: int,
    limit: int = settings.max_history_messages,
) -> List[dict]:
    result = await session.execute(
        select(Message)
        .where(Message.user_id == user_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return [{"role": m.role, "content": m.content} for m in reversed(rows)]


async def clear_history(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        delete(Message).where(Message.user_id == user_id)
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
    """Upsert a memory fact (INSERT … ON CONFLICT DO UPDATE)."""
    stmt = (
        pg_insert(Memory)
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
) -> List[Memory]:
    result = await session.execute(
        select(Memory)
        .where(Memory.user_id == user_id)
        .order_by(Memory.updated_at.desc())
        .limit(settings.max_memory_items)
    )
    return result.scalars().all()


async def delete_memory(
    session: AsyncSession,
    user_id: int,
    key: str,
) -> bool:
    result = await session.execute(
        delete(Memory).where(Memory.user_id == user_id, Memory.key == key)
    )
    await session.commit()
    return result.rowcount > 0


async def format_memory_context(session: AsyncSession, user_id: int) -> str:
    memories = await get_memories(session, user_id)
    if not memories:
        return "Нет сохранённых данных о пользователе."
    lines = [f"• {m.key}: {m.value}" for m in memories]
    return "\n".join(lines)


async def extract_and_save_memories(
    session: AsyncSession,
    user_id: int,
    user_message: str,
    assistant_reply: str,
) -> None:
    """
    Simple heuristic: scan for explicit save triggers in user message.
    Format: "запомни: <key> = <value>" or "remember: <key> = <value>"
    """
    triggers = ["запомни:", "remember:", "запомни,", "сохрани:"]
    lower = user_message.lower()
    for trigger in triggers:
        if trigger in lower:
            rest = user_message[lower.index(trigger) + len(trigger):].strip()
            if "=" in rest:
                key, _, value = rest.partition("=")
                key = key.strip()[:128]
                value = value.strip()[:1000]
                if key and value:
                    await save_memory(session, user_id, key, value)
                    logger.info("Memory saved for user %d: %s", user_id, key)
            break
