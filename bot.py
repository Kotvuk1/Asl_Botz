"""
Бакыт — персональный финансовый помощник.
Entry point: запускается через polling (совместимо с PythonAnywhere).
"""

import asyncio
import logging
from typing import List

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import settings
from core.db import init_db, close_db
from core.utils import setup_logging
from core.whitelist import init_whitelist
from handlers.commands import router as commands_router
from handlers.messages import router as messages_router

logger = logging.getLogger(__name__)

# Background task handles (kept so they can be cancelled on shutdown)
_bg_tasks: List[asyncio.Task] = []


def _run_migrations() -> None:
    """Apply pending Alembic migrations at startup (safe to run every time)."""
    try:
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            logger.info("Alembic migrations OK: %s", result.stdout.strip() or "up to date")
        else:
            logger.error("Alembic migration failed:\n%s", result.stderr)
    except Exception as e:
        logger.error("Could not run migrations: %s", e)


async def on_startup(bot: Bot) -> None:
    _run_migrations()
    await init_db()

    # Load whitelist from DB into memory
    from sqlalchemy import select
    from database.models import BakUser
    from core.db import AsyncSessionFactory
    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(BakUser.id).where(BakUser.is_whitelisted == True)  # noqa: E712
        )
        db_whitelist = [row[0] for row in result.all()]
    init_whitelist(db_whitelist)

    me = await bot.get_me()
    logger.info("Bot started: @%s (id=%d)", me.username, me.id)
    logger.info("Bot name: %s", settings.bot_name)
    logger.info("Owner ID: %d", settings.owner_id)
    logger.info("Allowed users: %s", settings.allowed_user_ids)
    logger.info("Groq model: %s", settings.groq_model)

    # Set bot commands
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="start",    description="Начать"),
        BotCommand(command="help",     description="Помощь"),
        BotCommand(command="clear",    description="Очистить историю"),
        BotCommand(command="memory",   description="🧠 Моя память о тебе"),
        BotCommand(command="remember", description="Запомнить факт"),
        BotCommand(command="forget",   description="Забыть факт"),
        BotCommand(command="balance",  description="💰 Текущий баланс"),
        BotCommand(command="add",      description="Добавить транзакцию"),
        BotCommand(command="history",  description="📋 Последние транзакции"),
        BotCommand(command="stats",    description="📊 Статистика расходов"),
        BotCommand(command="budget",   description="💼 Бюджет по категориям"),
        BotCommand(command="goals",    description="🎯 Финансовые цели"),
        BotCommand(command="addgoal",  description="Добавить финансовую цель"),
        BotCommand(command="goalset",  description="Пополнить цель"),
        BotCommand(command="goaldone", description="Завершить цель"),
        BotCommand(command="export",   description="📦 Экспорт данных"),
    ])
    logger.info("Bot commands registered.")


async def on_shutdown(bot: Bot) -> None:
    for task in _bg_tasks:
        task.cancel()
    if _bg_tasks:
        await asyncio.gather(*_bg_tasks, return_exceptions=True)
    await close_db()
    logger.info("Bot stopped. DB connection closed.")


async def main() -> None:
    setup_logging()
    logger.info("Starting %s bot...", settings.bot_name)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher(storage=MemoryStorage())

    # Register lifecycle hooks
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Register routers (order matters: commands before messages)
    dp.include_router(commands_router)
    dp.include_router(messages_router)

    logger.info("Starting polling...")
    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
