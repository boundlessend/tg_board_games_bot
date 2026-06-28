import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from aiogram.types import CallbackQuery, Message, TelegramObject

from constants import TELEGRAM_MESSAGE_LIMIT
from database import DatabaseError

logger = logging.getLogger(__name__)

_Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]
_Middleware = Callable[[_Handler, TelegramObject, dict[str, Any]], Awaitable[Any]]


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


def pick_unique[T](
    pool: list[T], issued: set[str], get_id: Callable[[T], str]
) -> T | None:
    """выбирает элемент без повтора в сессии, сбрасывая круг при исчерпании"""
    if len(pool) == 0:
        return None
    available = [item for item in pool if get_id(item) not in issued]
    if len(available) == 0:
        issued.clear()
        available = list(pool)
    chosen = random.choice(available)
    issued.add(get_id(chosen))
    return chosen


def pick_word(pool: list[str], issued: set[str]) -> str:
    """выбирает слово без повтора в сессии (пул считается непустым)"""
    chosen = pick_unique(pool, issued, _identity)
    if chosen is None:
        raise ValueError("пул слов пуст")
    return chosen


def _identity(value: str) -> str:
    """возвращает строку как собственный идентификатор"""
    return value


def split_report(report: str) -> list[str]:
    """делит длинный отчёт на сообщения telegram по лимиту длины"""
    chunks: list[str] = []
    current_lines: list[str] = []
    current_length = 0

    for line in report.splitlines():
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


def make_persist_middleware(
    persist: Callable[[], Awaitable[None]], scope: str
) -> _Middleware:
    """строит middleware: сохраняет снапшот сессий после обработки события"""

    async def middleware(
        handler: _Handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        result = await handler(event, data)
        try:
            await persist()
        except DatabaseError:
            logger.exception("session_persist_failed", extra={"scope": scope})
        return result

    return middleware


def make_chat_lock_middleware(locks: dict[int, asyncio.Lock]) -> _Middleware:
    """строит middleware: сериализует обработку событий одного чата блокировкой"""

    async def middleware(
        handler: _Handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        chat_id = _event_chat_id(event)
        if chat_id is None:
            return await handler(event, data)
        lock = locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            locks[chat_id] = lock
        async with lock:
            return await handler(event, data)

    return middleware


def _event_chat_id(event: TelegramObject) -> int | None:
    """возвращает id чата события (сообщение или callback)"""
    if isinstance(event, CallbackQuery):
        message = event.message
        return message.chat.id if isinstance(message, Message) else None
    if isinstance(event, Message):
        return event.chat.id
    return None
