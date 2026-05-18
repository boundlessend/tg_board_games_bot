import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import load_config
from database import SQLiteHistoryStorage
from handlers.admin import create_admin_router
from handlers.dangerous_words import create_dangerous_words_router
from handlers.start import start_router
from services.random_generator import load_dangerous_words_content


async def main() -> None:
    """запускает telegram-бота"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    config = load_config()
    content = load_dangerous_words_content(config.data_dir)
    storage = SQLiteHistoryStorage(config.database_path)
    await storage.initialize()

    bot = Bot(token=config.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(start_router)
    dispatcher.include_router(create_admin_router(content, storage))
    dispatcher.include_router(create_dangerous_words_router(content, storage))

    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
