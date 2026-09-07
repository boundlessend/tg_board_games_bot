import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    CallbackQuery,
    ChatMemberUpdated,
    ErrorEvent,
    Message,
    TelegramObject,
    Update,
)

from config import BotConfig, load_config
from constants import SESSION_TTL_DAYS
from database import DatabaseError, SQLiteHistoryStorage, iso_days_ago
from handlers.admin import create_admin_router
from handlers.bunker import create_bunker_router, restore_bunker_sessions
from handlers.common import persist_session
from handlers.content_admin import create_content_admin_router
from handlers.dangerous_group import (
    DangerousGroup,
    create_dangerous_group_router,
    restore_dangerous_sessions,
)
from handlers.dangerous_group import (
    persist_chat_session as persist_dangerous_session,
)
from handlers.favorites import create_favorites_router
from handlers.group_session import (
    GroupSession,
    cancel_session_timer,
    create_group_session_router,
    restore_group_sessions,
)
from handlers.group_session import (
    persist_chat_session as persist_group_session,
)
from handlers.inline import create_inline_router
from handlers.settings import create_settings_router
from handlers.start import create_start_router
from handlers.word_games import create_word_games_router
from health import HEARTBEAT_PATH, heartbeat_loop, touch_heartbeat, watchdog_loop
from logging_setup import configure_logging
from services.bunker import BunkerContent, load_bunker_content
from services.bunker_state import BunkerSession, SoloLobby, dump_lobby, dump_session
from services.content import (
    DangerousWordsContent,
    WordGame,
    load_dangerous_words_content,
    load_word_games,
)

logger = logging.getLogger(__name__)

_Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]
_Middleware = Callable[[_Handler, TelegramObject, dict[str, Any]], Awaitable[Any]]

# у роутера бункера запись снапшота живёт внутри замыкания, а scope объявлен
# приватной константой: переиспользовать нечего, поэтому литералы заданы здесь
_BUNKER_SCOPE = "bunker"
_BUNKER_LOBBY_SCOPE = "bunker_lobby"

ERROR_TEXT = "Что-то пошло не так. Попробуй ещё раз."
_THROTTLE_TEXT = "Слишком часто. Подожди секунду."
_THROTTLE_SECONDS = 0.4
_THROTTLE_CACHE_LIMIT = 1000
_TASKS_CONCURRENCY_LIMIT = 50
_ADMIN_ALERT_INTERVAL_SECONDS = 300.0
_CLEANUP_INTERVAL_SECONDS = 86400
_LEFT_STATUSES = frozenset({"kicked", "left"})

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
    throttle = make_throttle_middleware()
    dispatcher.message.outer_middleware(throttle)
    dispatcher.callback_query.outer_middleware(throttle)
    dispatcher.include_router(
        create_lifecycle_router(
            storage, group_sessions, dangerous_sessions, bunker_sessions
        )
    )
    dispatcher.include_router(create_start_router(word_games))
    dispatcher.include_router(create_settings_router(storage))
    dispatcher.include_router(create_favorites_router(storage))
    dispatcher.include_router(
        create_admin_router(storage, config.admin_ids, word_games)
    )
    dispatcher.include_router(
        create_content_admin_router(storage, config.admin_ids, word_games)
    )
    dispatcher.include_router(create_inline_router(content, storage))
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
    register_error_handler(dispatcher, bot, config.admin_ids)
    # хендлеры, прерванные отменой polling, могли не дойти до persist-middleware
    dispatcher.shutdown.register(
        partial(
            _save_snapshots,
            storage,
            group_sessions,
            dangerous_sessions,
            bunker_sessions,
            bunker_lobbies,
        )
    )

    # первая отметка до старта сторожа: иначе он убьёт процесс на прогреве
    touch_heartbeat(HEARTBEAT_PATH)
    tasks = (
        asyncio.create_task(
            heartbeat_loop(partial(_telegram_reachable, bot)), name="heartbeat"
        ),
        asyncio.create_task(watchdog_loop(), name="watchdog"),
        asyncio.create_task(_stale_cleanup_loop(storage), name="session_cleanup"),
    )
    for task in tasks:
        task.add_done_callback(_log_task_failure)

    await _notify_admins(bot, config.admin_ids, "Бот запущен.")
    # суточный backlog нажатий проигрывать нельзя: партии уже в другом
    # состоянии. drop_pending_updates живёт в delete_webhook, у start_polling
    # такого параметра нет
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dispatcher.start_polling(
            bot, tasks_concurrency_limit=_TASKS_CONCURRENCY_LIMIT
        )
    finally:
        for task in tasks:
            task.cancel()


def create_lifecycle_router(
    storage: SQLiteHistoryStorage,
    group_sessions: dict[int, GroupSession],
    dangerous_sessions: dict[int, DangerousGroup],
    bunker_sessions: dict[int, BunkerSession],
) -> Router:
    """создаёт роутер жизненного цикла чата: изгнание бота и апгрейд в супергруппу"""
    router = Router()

    @router.my_chat_member()
    async def handle_membership(event: ChatMemberUpdated) -> None:
        """убирает партии чата, из которого бота выгнали"""
        if event.new_chat_member.status not in _LEFT_STATUSES:
            return
        chat_id = event.chat.id
        group_session = group_sessions.pop(chat_id, None)
        if group_session is not None:
            # иначе таймер один раз выстрелит и напишет в чат, откуда выгнали
            cancel_session_timer(group_session)
        dangerous_sessions.pop(chat_id, None)
        bunker_sessions.pop(chat_id, None)
        await _persist_chat(
            storage, group_sessions, dangerous_sessions, bunker_sessions, chat_id
        )
        logger.info("chat_sessions_dropped", extra={"chat_id": chat_id})

    @router.message(F.migrate_to_chat_id)
    async def handle_migration(message: Message) -> None:
        """переносит партии на новый chat_id после апгрейда в супергруппу"""
        new_chat_id = message.migrate_to_chat_id
        if new_chat_id is None:
            return
        old_chat_id = message.chat.id
        _move_chat_session(group_sessions, old_chat_id, new_chat_id)
        _move_chat_session(dangerous_sessions, old_chat_id, new_chat_id)
        _move_chat_session(bunker_sessions, old_chat_id, new_chat_id)
        group_session = group_sessions.get(new_chat_id)
        if group_session is not None:
            # таймер продолжил бы писать в покинутый чат по старому id
            cancel_session_timer(group_session)
        # обе партии помнят чат табло отдельно от ключа словаря, а id
        # сообщения из старого чата в новом не существует: сбрасываем,
        # чтобы табло переиздалось, а не упало на правке
        dangerous = dangerous_sessions.get(new_chat_id)
        if dangerous is not None:
            dangerous.board_chat_id = new_chat_id
            dangerous.board_message_id = None
        bunker = bunker_sessions.get(new_chat_id)
        if bunker is not None:
            bunker.board_chat_id = new_chat_id
            bunker.board_message_id = None
        await _persist_chat(
            storage, group_sessions, dangerous_sessions, bunker_sessions, old_chat_id
        )
        await _persist_chat(
            storage, group_sessions, dangerous_sessions, bunker_sessions, new_chat_id
        )
        logger.info(
            "chat_sessions_migrated",
            extra={"chat_id": old_chat_id, "new_chat_id": new_chat_id},
        )

    return router


def make_throttle_middleware() -> _Middleware:
    """строит outer-middleware: гасит слишком частые события одного игрока

    заодно двигает отметку живости: она должна отражать работу диспетчера,
    а не ход независимого от него таймера
    """
    last_seen: dict[tuple[int, int], float] = {}

    async def middleware(
        handler: _Handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        nonlocal last_seen
        try:
            touch_heartbeat(HEARTBEAT_PATH)
        except OSError:
            logger.warning(
                "heartbeat_write_failed", extra={"path": str(HEARTBEAT_PATH)}
            )

        key = _throttle_key(event)
        if key is None:
            return await handler(event, data)

        now = time.monotonic()
        previous = last_seen.get(key)
        if previous is not None and now - previous < _THROTTLE_SECONDS:
            if isinstance(event, CallbackQuery):
                await event.answer(_THROTTLE_TEXT)
            return None
        if len(last_seen) >= _THROTTLE_CACHE_LIMIT:
            # кэш растёт с числом чатов, поэтому остывшие записи выбрасываем
            last_seen = {
                seen_key: seen
                for seen_key, seen in last_seen.items()
                if now - seen < _THROTTLE_SECONDS
            }
        last_seen[key] = now
        return await handler(event, data)

    return middleware


def _throttle_key(event: TelegramObject) -> tuple[int, int] | None:
    """возвращает пару (чат, игрок) события или None, если её нет"""
    if isinstance(event, CallbackQuery):
        message = event.message
        if not isinstance(message, Message):
            return None
        return message.chat.id, event.from_user.id
    if isinstance(event, Message):
        user = event.from_user
        # служебное сообщение об апгрейде в супергруппу дросселировать нельзя:
        # потерянный перенос партий восстановить уже нечем
        if user is None or event.migrate_to_chat_id is not None:
            return None
        return event.chat.id, user.id
    return None


def _move_chat_session[S](
    sessions: dict[int, S], old_chat_id: int, new_chat_id: int
) -> None:
    """переносит партию чата на новый id, если она есть"""
    session = sessions.pop(old_chat_id, None)
    if session is not None:
        sessions[new_chat_id] = session


async def _persist_chat(
    storage: SQLiteHistoryStorage,
    group_sessions: dict[int, GroupSession],
    dangerous_sessions: dict[int, DangerousGroup],
    bunker_sessions: dict[int, BunkerSession],
    chat_id: int,
) -> None:
    """пишет снапшоты всех игр одного чата: пропавшая партия удаляется"""
    await persist_group_session(storage, group_sessions, chat_id)
    await persist_dangerous_session(storage, dangerous_sessions, chat_id)
    await persist_session(
        storage, _BUNKER_SCOPE, str(chat_id), bunker_sessions.get(chat_id), dump_session
    )


async def _save_snapshots(
    storage: SQLiteHistoryStorage,
    group_sessions: dict[int, GroupSession],
    dangerous_sessions: dict[int, DangerousGroup],
    bunker_sessions: dict[int, BunkerSession],
    bunker_lobbies: dict[str, SoloLobby],
) -> None:
    """дописывает снапшоты живых партий перед закрытием ресурсов"""
    try:
        for chat_id in group_sessions:
            await persist_group_session(storage, group_sessions, chat_id)
        for chat_id in dangerous_sessions:
            await persist_dangerous_session(storage, dangerous_sessions, chat_id)
        for chat_id, bunker in bunker_sessions.items():
            await persist_session(
                storage, _BUNKER_SCOPE, str(chat_id), bunker, dump_session
            )
        for code, lobby in bunker_lobbies.items():
            await persist_session(storage, _BUNKER_LOBBY_SCOPE, code, lobby, dump_lobby)
    except DatabaseError:
        logger.exception("shutdown_persist_failed")


async def _stale_cleanup_loop(storage: SQLiteHistoryStorage) -> None:
    """раз в сутки убирает снапшоты брошенных партий

    без этого бот, живущий месяцами без рестарта, копит их до бесконечности:
    уборка на старте до него просто не доходит
    """
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
        try:
            removed = await storage.delete_stale_sessions(
                iso_days_ago(SESSION_TTL_DAYS)
            )
        except DatabaseError:
            # иначе одна занятая база выключает уборку до самого рестарта
            logger.exception("stale_cleanup_failed")
            continue
        if removed:
            logger.info("stale_sessions_removed", extra={"count": removed})


async def _telegram_reachable(bot: Bot) -> bool:
    """проверяет, что telegram отвечает боту"""
    try:
        await bot.get_me()
    except TelegramAPIError:
        logger.warning("telegram_probe_failed")
        return False
    return True


def _log_task_failure(task: asyncio.Task[None]) -> None:
    """логирует падение фоновой задачи: общий обработчик ошибок их не видит"""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error(
            "background_task_failed", exc_info=error, extra={"task": task.get_name()}
        )


async def _notify_admins(bot: Bot, admin_ids: frozenset[int], text: str) -> None:
    """сообщает администраторам о старте: молчаливый перезапуск незаметен"""
    for admin_id in sorted(admin_ids):
        try:
            await bot.send_message(admin_id, text)
        except TelegramAPIError:
            logger.warning("admin_notify_failed", extra={"telegram_id": admin_id})


def register_error_handler(
    dispatcher: Dispatcher, bot: Bot, admin_ids: frozenset[int]
) -> None:
    """вешает общий обработчик: логирует сбой, отвечает игроку и будит админов"""
    last_alert = 0.0

    @dispatcher.errors()
    async def handle_error(event: ErrorEvent) -> bool:
        nonlocal last_alert
        logger.exception(
            "handler_failed",
            exc_info=event.exception,
            extra={"update_id": event.update.update_id},
        )
        await _reply_about_error(event.update)
        now = time.monotonic()
        if now - last_alert >= _ADMIN_ALERT_INTERVAL_SECONDS:
            last_alert = now
            await _notify_admins(
                bot, admin_ids, f"Сбой обработчика: {type(event.exception).__name__}."
            )
        return True


async def _reply_about_error(update: Update) -> None:
    """сообщает о сбое туда, откуда пришёл апдейт"""
    callback = update.callback_query
    message = update.message
    try:
        if isinstance(callback, CallbackQuery):
            await callback.answer(ERROR_TEXT, show_alert=True)
        elif isinstance(message, Message):
            await message.answer(ERROR_TEXT)
    except TelegramAPIError:
        logger.warning("error_reply_failed")


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
