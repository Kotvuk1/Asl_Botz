"""
Асылхан — личный Telegram-бот.
Entry point: запускается через polling (совместимо с PythonAnywhere).
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import settings
from core.db import init_db, close_db
from core.utils import setup_logging
from handlers.commands import router as commands_router
from handlers.messages import router as messages_router

logger = logging.getLogger(__name__)


async def on_startup(bot: Bot) -> None:
    await init_db()
    me = await bot.get_me()
    logger.info("Bot started: @%s (id=%d)", me.username, me.id)
    logger.info("Owner ID: %d", settings.owner_id)
    logger.info("Allowed users: %s", settings.allowed_user_ids)
    logger.info("Groq model: %s", settings.groq_model)

    # Set bot commands
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать"),
        BotCommand(command="help", description="Помощь"),
        BotCommand(command="clear", description="Очистить историю"),
        BotCommand(command="memory", description="Моя память о тебе"),
        BotCommand(command="remember", description="Запомнить факт"),
        BotCommand(command="forget", description="Забыть факт"),
        BotCommand(command="tasks", description="Список задач"),
        BotCommand(command="addtask", description="Добавить задачу"),
        BotCommand(command="done", description="Отметить задачу выполненной"),
        BotCommand(command="deltask", description="Удалить задачу"),
    ])


async def on_shutdown(bot: Bot) -> None:
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
