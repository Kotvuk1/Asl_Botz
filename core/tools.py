"""
Task management tools used by command handlers.
"""

import logging
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Task

logger = logging.getLogger(__name__)


async def add_task(
    session: AsyncSession,
    user_id: int,
    title: str,
    description: Optional[str] = None,
) -> Task:
    task = Task(user_id=user_id, title=title, description=description)
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
        q = q.where(Task.is_done == False)  # noqa: E712
    q = q.order_by(Task.created_at.asc())
    result = await session.execute(q)
    return result.scalars().all()


async def complete_task(
    session: AsyncSession,
    user_id: int,
    task_id: int,
) -> bool:
    result = await session.execute(
        update(Task)
        .where(Task.id == task_id, Task.user_id == user_id)
        .values(is_done=True)
    )
    await session.commit()
    return result.rowcount > 0


async def delete_task(
    session: AsyncSession,
    user_id: int,
    task_id: int,
) -> bool:
    task = await session.get(Task, task_id)
    if task and task.user_id == user_id:
        await session.delete(task)
        await session.commit()
        return True
    return False


def format_tasks_list(tasks: List[Task]) -> str:
    if not tasks:
        return "✅ Список задач пуст!"
    lines = ["📋 <b>Ваши задачи:</b>\n"]
    for t in tasks:
        status = "✅" if t.is_done else "⬜"
        desc = f"\n   <i>{t.description}</i>" if t.description else ""
        lines.append(f"{status} <b>#{t.id}</b> {t.title}{desc}")
    return "\n".join(lines)
