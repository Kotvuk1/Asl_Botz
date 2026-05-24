import logging
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Task

logger = logging.getLogger(__name__)

TODO = "todo"
IN_PROGRESS = "in_progress"
DONE = "done"


async def add_task(
    session: AsyncSession,
    user_id: int,
    title: str,
    description: Optional[str] = None,
) -> Task:
    task = Task(user_id=user_id, title=title, description=description, status=TODO)
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


def _task_line(task: Task) -> str:
    desc = f"\n   ┗ <i>{task.description}</i>" if task.description else ""
    return f"<b>#{task.id}</b> {task.title}{desc}"


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

    status_icon = {DONE: "✅", IN_PROGRESS: "🔄", TODO: "⬜"}
    lines = ["📋 <b>Ваши задачи:</b>\n"]
    for t in tasks:
        icon = status_icon.get(t.status, "⬜")
        desc = f"\n   <i>{t.description}</i>" if t.description else ""
        lines.append(f"{icon} <b>#{t.id}</b> {t.title}{desc}")
    return "\n".join(lines)
