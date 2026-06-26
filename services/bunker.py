import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BunkerContentError(RuntimeError):
    """ошибка загрузки или структуры контента игры бункер"""

    pass


@dataclass(frozen=True)
class BunkerContent:
    """пулы карт игры бункер"""

    catastrophes: list[str]
    superpowers: list[str]
    phobias: list[str]
    character: list[str]
    hobby: list[str]
    baggage: list[str]
    facts: list[str]
    special_conditions: list[str]
    threats: list[str]
    bunker_items: list[str]


@dataclass(frozen=True)
class PlayerHand:
    """набор карт персонажа одного игрока"""

    superpower: str
    phobia: str
    character: str
    hobby: str
    baggage: str
    fact: str
    special_condition: str


@dataclass(frozen=True)
class RoundsPlan:
    """план раундов по числу игроков"""

    votes_per_round: tuple[int, int, int, int, int]
    exclusions: int
    seats: int


_CHARACTER_CATEGORIES: tuple[str, ...] = (
    "superpowers",
    "phobias",
    "character",
    "hobby",
    "baggage",
    "facts",
    "special_conditions",
)

MIN_PLAYERS = 4
MAX_PLAYERS = 16

# число голосований в каждом из 5 раундов по числу игроков (таблица раундов
# из правил); изгнанных = сумма голосований, мест в бункере = игроки - изгнанные
_VOTES_BY_COUNT: dict[int, tuple[int, int, int, int, int]] = {
    4: (0, 0, 0, 1, 1),
    5: (0, 0, 1, 1, 1),
    6: (0, 0, 1, 1, 1),
    7: (0, 1, 1, 1, 1),
    8: (0, 1, 1, 1, 1),
    9: (0, 1, 1, 1, 2),
    10: (0, 1, 1, 1, 2),
    11: (0, 1, 1, 2, 2),
    12: (0, 1, 1, 2, 2),
    13: (0, 1, 2, 2, 2),
    14: (0, 1, 2, 2, 2),
    15: (0, 2, 2, 2, 2),
    16: (0, 2, 2, 2, 2),
}


def load_bunker_content(data_dir: Path) -> BunkerContent:
    """загружает и проверяет контент игры бункер из bunker.json"""
    file_path = data_dir / "bunker.json"
    if not file_path.exists():
        raise BunkerContentError(f"JSON-файл не найден: {file_path}")
    try:
        with file_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as error:
        raise BunkerContentError(
            f"JSON-файл имеет неверный формат: {file_path}"
        ) from error

    if not isinstance(data, dict):
        raise BunkerContentError("bunker.json должен быть объектом.")

    return BunkerContent(
        catastrophes=_read_pool(data, "catastrophes"),
        superpowers=_read_pool(data, "superpowers"),
        phobias=_read_pool(data, "phobias"),
        character=_read_pool(data, "character"),
        hobby=_read_pool(data, "hobby"),
        baggage=_read_pool(data, "baggage"),
        facts=_read_pool(data, "facts"),
        special_conditions=_read_pool(data, "special_conditions"),
        threats=_read_pool(data, "threats"),
        bunker_items=_read_pool(data, "bunker_items"),
    )


def _read_pool(data: dict[str, Any], key: str) -> list[str]:
    """читает и проверяет один пул карт без повторов"""
    value = data.get(key)
    if not isinstance(value, list) or len(value) == 0:
        raise BunkerContentError(f"bunker.json: {key} должен быть непустым списком.")

    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or item.strip() == "":
            raise BunkerContentError(f"bunker.json: {key} содержит неверный элемент.")
        items.append(item.strip())

    if len(items) != len(set(items)):
        raise BunkerContentError(f"bunker.json: {key} содержит повторы.")
    return items


def deal_hands(content: BunkerContent, player_count: int) -> list[PlayerHand]:
    """раздаёт каждому игроку набор карт персонажа без повторов в партии"""
    if player_count < 1:
        raise BunkerContentError("Число игроков должно быть положительным.")
    for category in _CHARACTER_CATEGORIES:
        pool = getattr(content, category)
        if len(pool) < player_count:
            raise BunkerContentError(
                f"bunker.json: в пуле {category} меньше карт, чем игроков."
            )

    drawn = {
        category: random.sample(getattr(content, category), player_count)
        for category in _CHARACTER_CATEGORIES
    }
    return [
        PlayerHand(
            superpower=drawn["superpowers"][index],
            phobia=drawn["phobias"][index],
            character=drawn["character"][index],
            hobby=drawn["hobby"][index],
            baggage=drawn["baggage"][index],
            fact=drawn["facts"][index],
            special_condition=drawn["special_conditions"][index],
        )
        for index in range(player_count)
    ]


def pick_pairs(
    content: BunkerContent, count: int
) -> list[tuple[str, str]]:
    """выбирает пары карт бункера и угроз без повторов"""
    if len(content.bunker_items) < count or len(content.threats) < count:
        raise BunkerContentError("Недостаточно карт бункера или угроз для пар.")
    items = random.sample(content.bunker_items, count)
    threats = random.sample(content.threats, count)
    return list(zip(items, threats, strict=True))


def pick_catastrophe(content: BunkerContent) -> str:
    """выбирает случайную катастрофу"""
    return random.choice(content.catastrophes)


def rounds_plan(player_count: int) -> RoundsPlan:
    """возвращает план голосований, изгнаний и мест для числа игроков"""
    votes = _VOTES_BY_COUNT.get(player_count)
    if votes is None:
        raise BunkerContentError(
            f"Бункер поддерживает от {MIN_PLAYERS} до {MAX_PLAYERS} игроков."
        )
    exclusions = sum(votes)
    return RoundsPlan(
        votes_per_round=votes,
        exclusions=exclusions,
        seats=player_count - exclusions,
    )


def vote_leaders(votes: dict[int, int]) -> tuple[list[int], int]:
    """возвращает кандидатов с наибольшим числом голосов и это число"""
    if len(votes) == 0:
        return [], 0
    counts: dict[int, int] = {}
    for candidate in votes.values():
        counts[candidate] = counts.get(candidate, 0) + 1
    top = max(counts.values())
    leaders = [candidate for candidate, count in counts.items() if count == top]
    return leaders, top
