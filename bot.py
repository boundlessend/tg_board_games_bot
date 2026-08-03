import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    CallbackQuery,
    ErrorEvent,
)

from config import BotConfig, load_config
from constants import SESSION_TTL_DAYS
from database import SQLiteHistoryStorage, iso_days_ago
from handlers.admin import create_admin_router
from handlers.bunker import create_bunker_router, restore_bunker_sessions
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
from health import heartbeat_loop
from logging_setup import configure_logging
from services.bunker import BunkerContent, load_bunker_content
from services.bunker_state import BunkerSession, SoloLobby
from services.content import (
    DangerousWordsContent,
    WordGame,
    load_dangerous_words_content,
    load_word_games,
)

logger = logging.getLogger(__name__)

PRIVATE_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "Открыть меню"),
    ("help", "Справка по боту"),
    ("bunker", "Бункер: игра по коду в личке"),
    ("joinbunker", "Войти в бункер по коду"),
    ("fav", "Сохранить последнее слово"),
    ("favorites", "Показать избранное"),
    ("favclear", "Очистить избранное"),
    ("forgetme", "Удалить мои данные"),
)

GROUP_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "Открыть меню беседы"),
    ("help", "Справка по боту"),
    ("play", "Выбрать командную игру"),
    ("bunker", "Открыть «Бункер»"),
)


async def main() -> None:
    """запускает telegram-бота"""
    configure_logging(logging.INFO)

    config = load_config()
    content = load_dangerous_words_content(config.data_dir)
    word_games = load_word_games(config.data_dir)
    bunker_content = load_bunker_content(config.data_dir)
    storage = SQLiteHistoryStorage(config.database_path)
    await storage.initialize()

    # брошенные партии убираем до восстановления, иначе они займут свои чаты
    stale = await storage.delete_stale_sessions(iso_days_ago(SESSION_TTL_DAYS))
    if stale:
        logger.info("stale_sessions_removed", extra={"count": stale})

    group_sessions: dict[int, GroupSession] = {}
    await restore_group_sessions(storage, word_games, group_sessions)
    dangerous_sessions: dict[int, DangerousGroup] = {}
    await restore_dangerous_sessions(storage, dangerous_sessions)
    bunker_sessions: dict[int, BunkerSession] = {}
    bunker_lobbies: dict[str, SoloLobby] = {}
    bunker_member_lobby: dict[int, str] = {}
    await restore_bunker_sessions(
        storage, bunker_sessions, bunker_lobbies, bunker_member_lobby
    )

    bot = Bot(token=config.bot_token)
    # всё, что после создания бота, идёт под finally: иначе падение на
    # первом же запросе (например неверный токен) оставит http-сессию открытой
    try:
        await _run(
            bot,
            storage,
            config,
            content,
            word_games,
            bunker_content,
            group_sessions,
            dangerous_sessions,
            bunker_sessions,
            bunker_lobbies,
            bunker_member_lobby,
        )
    finally:
        await bot.session.close()
        # соединения sqlite закрываем явно, иначе движок остаётся висеть
        await storage.dispose()


async def _run(
    bot: Bot,
    storage: SQLiteHistoryStorage,
    config: BotConfig,
    content: DangerousWordsContent,
    word_games: list[WordGame],
    bunker_content: BunkerContent,
    group_sessions: dict[int, GroupSession],
    dangerous_sessions: dict[int, DangerousGroup],
    bunker_sessions: dict[int, BunkerSession],
    bunker_lobbies: dict[str, SoloLobby],
    bunker_member_lobby: dict[int, str],
) -> None:
    """собирает роутеры и крутит polling до остановки"""
    bot_username = (await bot.get_me()).username or ""
    await _publish_commands(bot)

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
        create_group_session_router(word_games, storage, group_sessions, bot_username)
    )
    dispatcher.include_router(
        create_bunker_router(
            bunker_content,
            storage,
            bunker_sessions,
            bunker_lobbies,
            bunker_member_lobby,
            bot_username,
        )
    )
    dispatcher.include_router(
        create_dangerous_group_router(content, storage, dangerous_sessions)
    )
    _register_error_handler(dispatcher)

    heartbeat = asyncio.create_task(heartbeat_loop())
    await _notify_admins(bot, config.admin_ids, "Бот запущен.")
    try:
        await dispatcher.start_polling(bot)
    finally:
        heartbeat.cancel()


async def _notify_admins(bot: Bot, admin_ids: frozenset[int], text: str) -> None:
    """сообщает администраторам о старте: молчаливый перезапуск незаметен"""
    for admin_id in sorted(admin_ids):
        try:
            await bot.send_message(admin_id, text)
        except TelegramAPIError:
            logger.warning("admin_notify_failed", extra={"telegram_id": admin_id})


def _register_error_handler(dispatcher: Dispatcher) -> None:
    """вешает общий обработчик: логирует сбой и снимает «часики» с кнопки"""

    @dispatcher.errors()
    async def handle_error(event: ErrorEvent) -> bool:
        logger.exception(
            "handler_failed",
            exc_info=event.exception,
            extra={"update_id": event.update.update_id},
        )
        callback = event.update.callback_query
        if isinstance(callback, CallbackQuery):
            try:
                await callback.answer(
                    "Что-то пошло не так. Попробуйте ещё раз.", show_alert=True
                )
            except TelegramAPIError:
                logger.warning("error_reply_failed")
        return True


async def _publish_commands(bot: Bot) -> None:
    """публикует список команд бота отдельно для лички и бесед"""
    await bot.set_my_commands(
        [BotCommand(command=name, description=text) for name, text in PRIVATE_COMMANDS],
        scope=BotCommandScopeAllPrivateChats(),
    )
    await bot.set_my_commands(
        [BotCommand(command=name, description=text) for name, text in GROUP_COMMANDS],
        scope=BotCommandScopeAllGroupChats(),
    )


if __name__ == "__main__":
    asyncio.run(main())
