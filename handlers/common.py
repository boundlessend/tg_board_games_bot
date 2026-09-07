import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from constants import TELEGRAM_MESSAGE_LIMIT
from database import DatabaseError, SQLiteHistoryStorage

logger = logging.getLogger(__name__)

_Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]
_Middleware = Callable[[_Handler, TelegramObject, dict[str, Any]], Awaitable[Any]]

_SEND_ATTEMPTS = 3
_RETRY_PAUSE_SECONDS = 1.0


def data_startswith(prefix: str) -> Callable[[CallbackQuery], bool]:
    """фильтр callback по префиксу данных"""

    def check(callback: CallbackQuery) -> bool:
        return callback.data is not None and callback.data.startswith(prefix)

    return check


def lookup_chat_session[S](
    callback: CallbackQuery, sessions: Mapping[int, S]
) -> tuple[S | None, int | None]:
    """находит сессию чата по сообщению callback"""
    message = callback.message
    if not isinstance(message, Message):
        return None, None
    chat_id = message.chat.id
    return sessions.get(chat_id), chat_id


def is_private_admin(message: Message, admin_ids: frozenset[int]) -> bool:
    """проверяет что сообщение от администратора из личного чата"""
    if message.chat.type != "private":
        return False
    if message.from_user is None:
        return False
    return message.from_user.id in admin_ids


def is_private_admin_callback(
    callback: CallbackQuery, admin_ids: frozenset[int]
) -> bool:
    """проверяет что callback от администратора из личного чата"""
    message = callback.message
    if message is None or message.chat.type != "private":
        return False
    return callback.from_user.id in admin_ids


def split_report(report: str) -> list[str]:
    """делит длинный отчёт на сообщения telegram по лимиту длины"""
    chunks: list[str] = []
    current_lines: list[str] = []
    current_length = 0

    for raw_line in report.splitlines():
        for line in _hard_wrap(raw_line):
            line_length = len(line) + 1
            if (
                current_length + line_length > TELEGRAM_MESSAGE_LIMIT
                and len(current_lines) > 0
            ):
                chunks.append("\n".join(current_lines))
                current_lines = []
                current_length = 0
            current_lines.append(line)
            current_length += line_length

    if len(current_lines) > 0:
        chunks.append("\n".join(current_lines))

    return chunks


def _hard_wrap(line: str) -> list[str]:
    """режет строку длиннее лимита сообщения telegram на части по лимиту"""
    if len(line) <= TELEGRAM_MESSAGE_LIMIT:
        return [line]
    return [
        line[start : start + TELEGRAM_MESSAGE_LIMIT]
        for start in range(0, len(line), TELEGRAM_MESSAGE_LIMIT)
    ]


def is_not_modified(error: TelegramBadRequest) -> bool:
    """отличает штатный отказ telegram править неизменившееся сообщение"""
    return "message is not modified" in str(error).lower()


async def persist_session[S](
    storage: SQLiteHistoryStorage,
    scope: str,
    key: str,
    session: S | None,
    dump: Callable[[S], dict[str, Any]],
) -> None:
    """пишет снапшот сессии в scope, а если сессии больше нет - удаляет строку"""
    if session is None:
        await storage.delete_session(scope, key)
        return
    await storage.save_session(scope, key, json.dumps(dump(session)))


async def restore_sessions[K, S](
    storage: SQLiteHistoryStorage,
    scope: str,
    parse_key: Callable[[str], K],
    load: Callable[[dict[str, Any]], S | None],
) -> dict[K, S]:
    """читает снапшоты scope, пропуская повреждённые и несовместимые"""
    restored: dict[K, S] = {}
    for key, data in (await storage.load_session_scope(scope)).items():
        try:
            session = load(json.loads(data))
            parsed_key = parse_key(key)
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            # снапшот несовместимой/повреждённой схемы - пропускаем
            logger.exception(
                "session_restore_failed",
                extra={"scope": scope, "key": key},
            )
            continue
        if session is not None:
            restored[parsed_key] = session
    return restored


def make_chat_persist_middleware(
    persist_chat: Callable[[int], Awaitable[None]], scope: str
) -> _Middleware:
    """строит middleware: сохраняет снапшот сессии чата события после обработки

    снапшот пишется и когда хендлер упал: состояние уже могло измениться до
    исключения, и без сохранения это изменение потерялось бы
    """

    async def middleware(
        handler: _Handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        try:
            return await handler(event, data)
        finally:
            chat_id = _event_chat_id(event)
            if chat_id is not None:
                try:
                    await persist_chat(chat_id)
                except DatabaseError:
                    logger.exception("session_persist_failed", extra={"scope": scope})

    return middleware


class ChatLocks:
    """блокировки по чатам, освобождающие память после последнего ожидающего

    словарь локов рос бы монотонно с числом чатов за всё время жизни бота,
    поэтому запись живёт ровно пока есть желающие её захватить: счётчик
    ожидающих ведётся явно, потому что asyncio.Lock его не отдаёт
    """

    def __init__(self) -> None:
        """создаёт пустой реестр блокировок"""
        self._entries: dict[int, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def hold(self, chat_id: int) -> AsyncIterator[None]:
        """держит блокировку чата на время блока и убирает её за собой"""
        lock, waiters = self._entries.get(chat_id, (asyncio.Lock(), 0))
        self._entries[chat_id] = (lock, waiters + 1)
        try:
            async with lock:
                yield
        finally:
            self._drop_waiter(chat_id)

    def _drop_waiter(self, chat_id: int) -> None:
        """снимает учёт ожидающего и убирает освободившуюся блокировку"""
        entry = self._entries.get(chat_id)
        if entry is None:
            return
        lock, waiters = entry
        if waiters <= 1:
            del self._entries[chat_id]
            return
        self._entries[chat_id] = (lock, waiters - 1)

    def __len__(self) -> int:
        """возвращает число удерживаемых блокировок"""
        return len(self._entries)


def make_chat_lock_middleware(locks: ChatLocks) -> _Middleware:
    """строит middleware: сериализует обработку событий одного чата блокировкой"""

    async def middleware(
        handler: _Handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        chat_id = _event_chat_id(event)
        if chat_id is None:
            return await handler(event, data)
        async with locks.hold(chat_id):
            return await handler(event, data)

    return middleware


async def is_chat_manager(bot: Bot, chat_id: int, user_id: int, host_id: int) -> bool:
    """проверяет право управлять партией: создатель либо админ чата

    нужен, чтобы партия не оставалась навсегда заблокированной, если
    создатель ушёл из чата
    """
    if user_id == host_id:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError):
        logger.warning("chat_member_lookup_failed", extra={"chat_id": chat_id})
        return False
    return member.status in ("administrator", "creator")


async def send_with_retry(
    bot: Bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message | None:
    """шлёт сообщение, переживая флуд-контроль и сетевые сбои

    возвращает None, если чат недоступен (бота выгнали, чат удалён): это
    ожидаемое состояние, а не сбой. Прочие ошибки после исчерпания попыток
    пробрасываются наверх, где их подхватывает общий обработчик ошибок
    """
    last_error: Exception | None = None
    for attempt in range(_SEND_ATTEMPTS):
        try:
            return await bot.send_message(chat_id, text, reply_markup=reply_markup)
        except TelegramForbiddenError:
            logger.warning("chat_unreachable", extra={"chat_id": chat_id})
            return None
        except TelegramRetryAfter as error:
            last_error = error
            logger.warning(
                "flood_control",
                extra={"chat_id": chat_id, "retry_after": error.retry_after},
            )
            await asyncio.sleep(error.retry_after)
        except TelegramNetworkError as error:
            last_error = error
            logger.warning(
                "network_error", extra={"chat_id": chat_id, "attempt": attempt}
            )
            await asyncio.sleep(_RETRY_PAUSE_SECONDS)
    raise _unreachable(last_error)


def _unreachable(last_error: Exception | None) -> Exception:
    """возвращает ошибку для проброса после исчерпания попыток"""
    if last_error is None:
        return RuntimeError("отправка сообщения не удалась без причины")
    return last_error


def _event_chat_id(event: TelegramObject) -> int | None:
    """возвращает id чата события (сообщение или callback)"""
    if isinstance(event, CallbackQuery):
        message = event.message
        return message.chat.id if isinstance(message, Message) else None
    if isinstance(event, Message):
        return event.chat.id
    return None
