"""проверки загрузки контента и разбора конфигурации

размеры пулов проверяются как инварианты, а не точными числами: иначе
каждое добавленное слово ломало бы тест
"""

from pathlib import Path

from config import _parse_admin_ids, _resolve_database_path
from services.bunker import MAX_PLAYERS, BunkerContent
from services.content import (
    DangerousWordsContent,
    WordGame,
    group_games,
    private_games,
)

MIN_WORDS_PER_GAME = 100


def test_dangerous_content_has_no_duplicates(
    dangerous_content: DangerousWordsContent,
) -> None:
    """слова, проклятия и боссы загружены и уникальны"""
    assert len(dangerous_content.words) >= MIN_WORDS_PER_GAME
    assert len(dangerous_content.words) == len(set(dangerous_content.words))

    curse_ids = [curse.id for curse in dangerous_content.curses]
    assert curse_ids and len(curse_ids) == len(set(curse_ids))

    boss_ids = [boss.id for boss in dangerous_content.bosses]
    assert boss_ids and len(boss_ids) == len(set(boss_ids))


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
