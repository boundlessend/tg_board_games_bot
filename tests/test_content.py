"""проверки загрузки контента и разбора конфигурации

размеры пулов проверяются как инварианты, а не точными числами: иначе
каждое добавленное слово ломало бы тест
"""

import json
import shutil
from pathlib import Path

import pytest

from config import _parse_admin_ids, _resolve_database_path
from services.bunker import MAX_PLAYERS, BunkerContent
from services.content import (
    DangerousWordsContent,
    DataFileError,
    WordGame,
    all_dangerous_words,
    group_games,
    load_dangerous_words_content,
    private_games,
)

MIN_WORDS_PER_GAME = 100


def test_dangerous_content_has_no_duplicates(
    dangerous_content: DangerousWordsContent,
) -> None:
    """слова разложены по всем категориям без повторов, проклятия и боссы уникальны"""
    for words in dangerous_content.categories.values():
        assert len(words) >= MIN_WORDS_PER_GAME
    words = all_dangerous_words(dangerous_content)
    # «ёж» и «еж» в игре одно слово: повтор не должен прятаться за буквой ё
    assert len({word.replace("ё", "е") for word in words}) == len(words)

    curse_ids = [curse.id for curse in dangerous_content.curses]
    assert curse_ids and len(curse_ids) == len(set(curse_ids))

    boss_ids = [boss.id for boss in dangerous_content.bosses]
    assert boss_ids and len(boss_ids) == len(set(boss_ids))


_ONE_WORD_PER_CATEGORY = {
    "nature": ["кот"],
    "fantasy": ["дракон"],
    "science": ["робот"],
    "culture": ["гитара"],
    "people": ["друг"],
}


@pytest.mark.parametrize(
    "words",
    [
        {"nature": ["кот"]},
        {**_ONE_WORD_PER_CATEGORY, "ordinary": ["стол"]},
        {**_ONE_WORD_PER_CATEGORY, "people": []},
        {**_ONE_WORD_PER_CATEGORY, "people": ["Кот "]},
    ],
    ids=["missing_category", "extra_category", "empty_category", "cross_duplicate"],
)
def test_words_loader_rejects_malformed_categories(
    tmp_path: Path, data_dir: Path, words: dict[str, list[str]]
) -> None:
    """битый words.json не грузится: без категории, с лишней, пустой или с повтором"""
    for name in ("curses.json", "bosses.json"):
        shutil.copy(data_dir / name, tmp_path / name)
    (tmp_path / "words.json").write_text(json.dumps(words), encoding="utf-8")

    with pytest.raises(DataFileError):
        load_dangerous_words_content(tmp_path)


def test_word_games_loaded_and_split_by_chat_type(
    word_games: list[WordGame],
) -> None:
    """игры делятся на личные и командные флагом private_only"""
    assert {game.game_id for game in word_games} == {"crocodile", "alias", "whoami"}
    for game in word_games:
        assert len(game.words) >= MIN_WORDS_PER_GAME
        lowered = [word.lower() for word in game.words]
        assert len(lowered) == len(set(lowered))

    assert [game.game_id for game in private_games(word_games)] == ["whoami"]
    assert [game.game_id for game in group_games(word_games)] == ["crocodile", "alias"]


def test_bunker_pools_cover_max_players(bunker_content: BunkerContent) -> None:
    """карт хватает на полный стол и пары раундов"""
    for category in ("superpowers", "phobias", "character", "hobby", "baggage"):
        assert len(getattr(bunker_content, category)) >= MAX_PLAYERS
    assert len(bunker_content.bunker_items) >= 5
    assert len(bunker_content.threats) >= 5


def test_resolve_database_path() -> None:
    """DATABASE_PATH из окружения переопределяет путь к базе"""
    project = Path("/proj")
    assert _resolve_database_path(None, project) == project / "bot.sqlite3"
    assert _resolve_database_path("  ", project) == project / "bot.sqlite3"
    assert _resolve_database_path("/db/bot.sqlite3", project) == Path("/db/bot.sqlite3")


def test_parse_admin_ids() -> None:
    """ADMIN_IDS парсится в множество telegram id"""
    assert _parse_admin_ids(None) == frozenset()
    assert _parse_admin_ids("") == frozenset()
    assert _parse_admin_ids("111, 222 ,333") == frozenset({111, 222, 333})
