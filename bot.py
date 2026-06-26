import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import load_config
from database import SQLiteHistoryStorage
from handlers.admin import create_admin_router
from handlers.content_admin import create_content_admin_router
from handlers.dangerous_words import create_dangerous_words_router
from handlers.favorites import create_favorites_router
from handlers.group_session import create_group_session_router
from handlers.inline import create_inline_router
from handlers.settings import create_settings_router
from handlers.start import create_start_router
from handlers.word_games import create_word_games_router
from services.random_generator import (
    load_dangerous_words_content,
    load_word_games,
)


async def main() -> None:
    """запускает telegram-бота"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    config = load_config()
    content = load_dangerous_words_content(config.data_dir)
    word_games = load_word_games(config.data_dir)
    storage = SQLiteHistoryStorage(config.database_path)
    await storage.initialize()

    bot = Bot(token=config.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(create_start_router(word_games))
    dispatcher.include_router(create_settings_router(storage))
    dispatcher.include_router(create_favorites_router(storage))
    dispatcher.include_router(
        create_admin_router(content, storage, config.admin_ids, word_games)
    )
    dispatcher.include_router(
        create_content_admin_router(storage, config.admin_ids, word_games)
    )
    dispatcher.include_router(create_inline_router(content))
    dispatcher.include_router(create_word_games_router(word_games, storage))
    dispatcher.include_router(
        create_group_session_router(word_games, storage)
    )
    dispatcher.include_router(create_dangerous_words_router(content, storage))

    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
