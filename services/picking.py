"""выбор игрового контента без повторов

механизма два, и они не взаимозаменяемы:

- select_unique_item - персональная история в SQLite, круг переживает
  перезапуск бота; используется в личных играх, где выдача привязана к
  telegram_id;
- pick_unique / pick_word - история в памяти сессии, живёт ровно партию и
  общая для всех её участников; используется в групповых играх, где круг
  считается на чат, а не на человека.
"""

import random
from collections.abc import Awaitable, Callable

from exceptions import DuplicateHistoryItemError
from services.content import EmptyPoolError


async def select_unique_item[T](
    items: list[T],
    get_item_id: Callable[[T], str],
    get_seen_ids: Callable[[int], Awaitable[set[str]]],
    save_seen_id: Callable[[int, str], Awaitable[None]],
    telegram_id: int,
) -> tuple[T, int]:
    """выбирает и сохраняет случайный элемент без повтора для пользователя

    возвращает пару (элемент, сколько всего выдано после сохранения):
    счётчик считается из уже загруженной истории и не требует отдельного
    запроса count в базу
    """
    seen_count, available_items = await _load_available(
        items, get_item_id, get_seen_ids, telegram_id
    )

    while len(available_items) > 0:
        selected_item = random.choice(available_items)
        try:
            await save_seen_id(telegram_id, get_item_id(selected_item))
            return selected_item, seen_count + 1
        except DuplicateHistoryItemError:
            # параллельная выдача успела занять элемент: перечитываем историю
            seen_count, available_items = await _load_available(
                items, get_item_id, get_seen_ids, telegram_id
            )

    raise EmptyPoolError("Пул доступных элементов пуст.")


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
    chosen = pick_unique(pool, issued, identity)
    if chosen is None:
        raise ValueError("пул слов пуст")
    return chosen


def identity(value: str) -> str:
    """возвращает строку как собственный идентификатор"""
    return value


async def _load_available[T](
    items: list[T],
    get_item_id: Callable[[T], str],
    get_seen_ids: Callable[[int], Awaitable[set[str]]],
    telegram_id: int,
) -> tuple[int, list[T]]:
    """возвращает размер истории и ещё не выданные пользователю элементы"""
    seen_ids = await get_seen_ids(telegram_id)
    available_items = [item for item in items if get_item_id(item) not in seen_ids]
    if len(available_items) == 0:
        raise EmptyPoolError("Пул доступных элементов пуст.")
    return len(seen_ids), available_items
