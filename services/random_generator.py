from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
import json
import random
from typing import Any

from exceptions import DuplicateHistoryItemError


class ContentError(RuntimeError):
    """ошибка загрузки или выбора игрового контента"""

    pass


class EmptyPoolError(ContentError):
    """нет доступных элементов для выдачи пользователю"""

    pass


class DataFileError(ContentError):
    """ошибка структуры или чтения json-файла с данными"""

    pass


@dataclass(frozen=True)
class Curse:
    """проклятье для игры опасные слова"""

    id: str
    title: str
    description: str


@dataclass(frozen=True)
class Boss:
    """босс для игры опасные слова"""

    id: str
    name: str
    description: str


@dataclass(frozen=True)
class DangerousWordsContent:
    """загруженный контент помощника опасные слова"""

    words: list[str]
    curses: list[Curse]
    bosses: list[Boss]


@dataclass(frozen=True)
class WordGame:
    """словесная игра с одним пулом слов"""

    game_id: str
    title: str
    words: list[str]


_WORD_GAME_FILES: tuple[tuple[str, str, str], ...] = (
    ("crocodile", "Крокодил", "crocodile.json"),
    ("alias", "Алиас", "alias.json"),
    ("whoami", "Кто я?", "whoami.json"),
    ("hat", "Шляпа", "hat.json"),
)


def load_word_games(data_dir: Path) -> list[WordGame]:
    """загружает словесные игры из их json-файлов"""
    games: list[WordGame] = []
    for game_id, title, file_name in _WORD_GAME_FILES:
        data = _read_json_file(data_dir / file_name)
        words = _parse_word_list(data, file_name)
        games.append(WordGame(game_id=game_id, title=title, words=words))
    return games


def _parse_word_list(data: Any, file_name: str) -> list[str]:
    """проверяет структуру списка слов словесной игры"""
    if not isinstance(data, dict):
        raise DataFileError(
            f"{file_name} должен быть объектом с ключом words."
        )

    value = data.get("words")
    if not isinstance(value, list):
        raise DataFileError(f"{file_name} должен содержать список words.")

    words: list[str] = []
    for item in value:
        if not isinstance(item, str) or item.strip() == "":
            raise DataFileError(f"{file_name} содержит неверное слово.")
        words.append(item.strip())

    if len(words) == 0:
        raise DataFileError(f"{file_name}: список слов пуст.")
    lowered = [word.lower() for word in words]
    if len(lowered) != len(set(lowered)):
        raise DataFileError(f"{file_name} содержит повторяющиеся слова.")

    return words


async def select_unique_item[T](
    items: list[T],
    get_item_id: Callable[[T], str],
    get_seen_ids: Callable[[int], Awaitable[set[str]]],
    save_seen_id: Callable[[int, str], Awaitable[None]],
    telegram_id: int,
) -> T:
    """выбирает и сохраняет случайный элемент без повтора для пользователя"""
    available_items = await _get_available_items(
        items, get_item_id, get_seen_ids, telegram_id
    )

    while len(available_items) > 0:
        selected_item = random.choice(available_items)
        try:
            await save_seen_id(telegram_id, get_item_id(selected_item))
            return selected_item
        except DuplicateHistoryItemError:
            available_items = await _get_available_items(
                items, get_item_id, get_seen_ids, telegram_id
            )

    raise EmptyPoolError("Пул доступных элементов пуст.")


def load_dangerous_words_content(data_dir: Path) -> DangerousWordsContent:
    """загружает все данные помощника опасные слова"""
    words_data = _read_json_file(data_dir / "words.json")
    curses_data = _read_json_file(data_dir / "curses.json")
    bosses_data = _read_json_file(data_dir / "bosses.json")

    words = _parse_words(words_data)
    curses = _parse_curses(curses_data)
    bosses = _parse_bosses(bosses_data)

    return DangerousWordsContent(words=words, curses=curses, bosses=bosses)


def _read_json_file(file_path: Path) -> Any:
    """читает json-файл и возвращает сырые данные"""
    if not file_path.exists():
        raise DataFileError(f"JSON-файл не найден: {file_path}")

    try:
        with file_path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError as error:
        raise DataFileError(
            f"JSON-файл имеет неверный формат: {file_path}"
        ) from error


def _parse_words(data: Any) -> list[str]:
    """проверяет структуру слов и объединяет оба списка"""
    if not isinstance(data, dict):
        raise DataFileError(
            "words.json должен быть объектом с ключами ordinary и fantasy."
        )

    ordinary = _read_words_group(data, "ordinary")
    fantasy = _read_words_group(data, "fantasy")
    words = ordinary + fantasy

    if len(words) == 0:
        raise DataFileError("Список слов пуст.")
    if len(words) != len(set(words)):
        raise DataFileError("words.json содержит повторяющиеся слова.")

    return words


def _read_words_group(data: dict[str, Any], group_name: str) -> list[str]:
    """читает одну тематическую группу слов"""
    value = data.get(group_name)
    if not isinstance(value, list):
        raise DataFileError(
            f"words.json должен содержать список {group_name}."
        )

    words: list[str] = []
    for item in value:
        if not isinstance(item, str) or item.strip() == "":
            raise DataFileError(
                f"words.json содержит неверное слово в списке {group_name}."
            )
        words.append(item.strip().lower())

    return words


def _parse_curses(data: Any) -> list[Curse]:
    """проверяет структуру проклятий"""
    if not isinstance(data, list):
        raise DataFileError("curses.json должен быть списком.")
    if len(data) == 0:
        raise DataFileError("Список проклятий пуст.")

    curses: list[Curse] = []
    for item in data:
        if not isinstance(item, dict):
            raise DataFileError("curses.json содержит элемент неверного типа.")
        curses.append(
            Curse(
                id=_read_required_string(item, "id", "curses.json"),
                title=_read_required_string(item, "title", "curses.json"),
                description=_read_required_string(
                    item, "description", "curses.json"
                ),
            )
        )

    _ensure_unique_ids([curse.id for curse in curses], "curses.json")
    return curses


def _parse_bosses(data: Any) -> list[Boss]:
    """проверяет структуру боссов"""
    if not isinstance(data, list):
        raise DataFileError("bosses.json должен быть списком.")
    if len(data) == 0:
        raise DataFileError("Список боссов пуст.")

    bosses: list[Boss] = []
    for item in data:
        if not isinstance(item, dict):
            raise DataFileError("bosses.json содержит элемент неверного типа.")
        bosses.append(
            Boss(
                id=_read_required_string(item, "id", "bosses.json"),
                name=_read_required_string(item, "name", "bosses.json"),
                description=_read_required_string(
                    item, "description", "bosses.json"
                ),
            )
        )

    _ensure_unique_ids([boss.id for boss in bosses], "bosses.json")
    return bosses


def _read_required_string(
    item: dict[str, Any], key: str, file_name: str
) -> str:
    """читает обязательное строковое поле json-объекта"""
    value = item.get(key)
    if not isinstance(value, str) or value.strip() == "":
        raise DataFileError(f"{file_name} содержит неверное поле {key}.")
    return value.strip()


def _ensure_unique_ids(ids: list[str], file_name: str) -> None:
    """проверяет уникальность идентификаторов в json-файле"""
    if len(ids) != len(set(ids)):
        raise DataFileError(f"{file_name} содержит повторяющиеся id.")


async def _get_available_items[T](
    items: list[T],
    get_item_id: Callable[[T], str],
    get_seen_ids: Callable[[int], Awaitable[set[str]]],
    telegram_id: int,
) -> list[T]:
    """возвращает элементы, которые ещё не выдавались пользователю"""
    seen_ids = await get_seen_ids(telegram_id)
    available_items = [
        item for item in items if get_item_id(item) not in seen_ids
    ]
    if len(available_items) == 0:
        raise EmptyPoolError("Пул доступных элементов пуст.")
    return available_items
