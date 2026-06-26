import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import load_config
from database import SQLiteHistoryStorage
from handlers.admin import create_admin_router
from handlers.bunker import create_bunker_router
from handlers.content_admin import create_content_admin_router
from handlers.dangerous_group import (
    DangerousGroup,
    create_dangerous_group_router,
    restore_dangerous_sessions,
)
from handlers.favorites import create_favorites_router
from handlers.group_session import (
    GroupSession,
    create_group_session_router,
    restore_group_sessions,
)
from handlers.inline import create_inline_router
from handlers.settings import create_settings_router
from handlers.start import create_start_router
from handlers.word_games import create_word_games_router
from services.bunker import load_bunker_content
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
    bunker_content = load_bunker_content(config.data_dir)
    storage = SQLiteHistoryStorage(config.database_path)
    await storage.initialize()

    group_sessions: dict[int, GroupSession] = {}
    await restore_group_sessions(storage, word_games, group_sessions)
    dangerous_sessions: dict[int, DangerousGroup] = {}
    await restore_dangerous_sessions(storage, dangerous_sessions)

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
        create_group_session_router(word_games, storage, group_sessions)
    )
    dispatcher.include_router(create_bunker_router(bunker_content))
    dispatcher.include_router(
        create_dangerous_group_router(content, storage, dangerous_sessions)
    )

    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
