"""smoke-проверка сборки и ключевой логики бота без сети и токена

запуск: pytest (из корня репозитория)
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Dispatcher  # noqa: E402

import keyboards  # noqa: E402
from config import _parse_admin_ids, _resolve_database_path  # noqa: E402
from database import SQLiteHistoryStorage, iso_days_ago  # noqa: E402
from handlers.admin import (  # noqa: E402
    _build_statistics_csv,
    _build_summary,
    create_admin_router,
)
from handlers.content_admin import (  # noqa: E402
    _addword_usage,
    _is_sqlite_file,
    _parse_custom_id,
    _parse_pair,
    _parse_words_pack,
    create_content_admin_router,
)
from handlers.dangerous_words import create_dangerous_words_router  # noqa: E402
from handlers.inline import create_inline_router  # noqa: E402
from handlers.start import create_start_router  # noqa: E402
from handlers.word_games import (  # noqa: E402
    _data_prefix,
    _resolve_game,
    _select_word_with_cycle,
    create_word_games_router,
)
from services.random_generator import (  # noqa: E402
    load_dangerous_words_content,
    load_word_games,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def test_config_resolves_database_path() -> None:
    """DATABASE_PATH из окружения переопределяет путь к базе"""
    project = Path("/proj")
    assert _resolve_database_path(None, project) == project / "bot.sqlite3"
    assert _resolve_database_path("  ", project) == project / "bot.sqlite3"
    assert _resolve_database_path("/db/bot.sqlite3", project) == Path(
        "/db/bot.sqlite3"
    )


def test_content_loads_and_validates() -> None:
    """контент опасных слов и словесных игр загружается без дублей"""
    content = load_dangerous_words_content(DATA_DIR)
    assert len(content.words) == 1000
    assert len(content.curses) == 100
    assert len(content.bosses) == 100

    games = load_word_games(DATA_DIR)
    assert [game.game_id for game in games] == [
        "crocodile",
        "alias",
        "whoami",
        "hat",
    ]
    for game in games:
        lowered = [word.lower() for word in game.words]
        assert len(lowered) == len(set(lowered))

    whoami = next(game for game in games if game.game_id == "whoami")
    assert "Гарри Поттер" in whoami.words


def test_pure_helpers() -> None:
    """чистые помощники парсинга и резолва работают корректно"""
    assert _parse_pair("назв | опис") == ("назв", "опис")
    assert _parse_pair("без разделителя") is None
    assert "crocodile" in _addword_usage({"dangerous_words", "crocodile"})

    assert _parse_custom_id("cc_3", "cc_") == 3
    assert _parse_custom_id("3", "cc_") == 3
    assert _parse_custom_id("abc", "cc_") is None
    assert _parse_words_pack(b'["a", "b"]') == ["a", "b"]
    assert _parse_words_pack(b"a\nb, c") == ["a", "b", "c"]

    games = load_word_games(DATA_DIR)
    games_by_id = {game.game_id: game for game in games}
    resolved = _resolve_game("wg:open:alias", "wg:open:", games_by_id)
    assert resolved is not None and resolved.game_id == "alias"
    assert _resolve_game("wg:open:zzz", "wg:open:", games_by_id) is None

    is_word = _data_prefix("wg:word:")

    class FakeCallback:
        data = "wg:word:crocodile"

    assert is_word(FakeCallback()) is True


async def _exercise_storage() -> None:
    """проверяет хранилище, no-repeat, авто-цикл и пользовательский контент"""
    db_path = Path(tempfile.mkdtemp()) / "db" / "smoke.sqlite3"
    assert not db_path.parent.exists()
    storage = SQLiteHistoryStorage(db_path)
    await storage.initialize()
    assert db_path.parent.exists()

    content = load_dangerous_words_content(DATA_DIR)
    games = load_word_games(DATA_DIR)
    crocodile = next(g for g in games if g.game_id == "crocodile")

    word_one, cycle_one = await _select_word_with_cycle(
        crocodile.words, storage, 5, "crocodile"
    )
    word_two, cycle_two = await _select_word_with_cycle(
        crocodile.words, storage, 5, "crocodile"
    )
    assert word_one != word_two
    assert cycle_one is False and cycle_two is False
    assert await storage.count_user_game_words(5, "crocodile") == 2

    pool = ["a", "b"]
    await _select_word_with_cycle(pool, storage, 9, "tg")
    await _select_word_with_cycle(pool, storage, 9, "tg")
    _, is_new_cycle = await _select_word_with_cycle(pool, storage, 9, "tg")
    assert is_new_cycle is True

    await storage.save_user_word(7, "слово")
    await storage.reset_user_all(7)
    assert await storage.count_user_words(7) == 0

    await storage.add_custom_word("crocodile", "кастом")
    assert "кастом" in await storage.get_custom_words("crocodile")
    duplicate_raised = False
    try:
        await storage.add_custom_word("crocodile", "кастом")
    except Exception as error:
        duplicate_raised = error.__class__.__name__ == "DuplicateHistoryItemError"
    assert duplicate_raised
    await storage.add_custom_curse("заголовок", "описание")
    custom_curses = await storage.get_custom_curses()
    assert custom_curses and custom_curses[0].id.startswith("cc_")
    await storage.add_custom_boss("имя", "описание")
    custom_bosses = await storage.get_custom_bosses()
    assert custom_bosses and custom_bosses[0].id.startswith("cb_")

    assert await storage.delete_custom_word("crocodile", "кастом") is True
    assert await storage.delete_custom_word("crocodile", "кастом") is False
    curse_id = int(custom_curses[0].id.removeprefix("cc_"))
    assert await storage.delete_custom_curse(curse_id) is True
    assert await storage.delete_custom_curse(curse_id) is False

    await storage.save_user_word(1, "альфа")
    await storage.save_user_word(2, "альфа")
    statistics = await storage.get_all_user_statistics()
    assert "альфа x2" in _build_summary(statistics, 0, 0)
    csv_text = _build_statistics_csv(statistics)
    assert csv_text.splitlines()[0] == "telegram_id,words,curses,bosses"

    assert await storage.count_issuances_since(iso_days_ago(1)) > 0
    assert await storage.count_active_users_since(iso_days_ago(1)) > 0
    by_day = await storage.issuances_by_day(iso_days_ago(1))
    assert by_day and by_day[-1][1] > 0
    assert await storage.count_issuances_since("9999-01-01T00:00:00+00:00") == 0

    dispatcher = Dispatcher()
    dispatcher.include_router(create_start_router(games))
    dispatcher.include_router(
        create_admin_router(content, storage, frozenset({1}), games)
    )
    dispatcher.include_router(
        create_content_admin_router(storage, frozenset({1}), games)
    )
    dispatcher.include_router(create_inline_router(content))
    dispatcher.include_router(create_word_games_router(games, storage))
    dispatcher.include_router(create_dangerous_words_router(content, storage))

    main_menu = keyboards.create_main_menu_keyboard(games)
    labels = [
        button.text
        for row in main_menu.inline_keyboard
        for button in row
    ]
    assert labels == ["Опасные слова", "Крокодил", "Алиас", "Кто я?", "Шляпа"]
    for keyboard in [main_menu, keyboards.create_word_game_keyboard("crocodile")]:
        for row in keyboard.inline_keyboard:
            for button in row:
                assert button.callback_data is not None
                assert len(button.callback_data.encode()) <= 64


def test_storage_and_wiring() -> None:
    """прогоняет асинхронные проверки хранилища и связки роутеров"""
    asyncio.run(_exercise_storage())


def test_parse_admin_ids() -> None:
    """ADMIN_IDS парсится в множество telegram id"""
    assert _parse_admin_ids(None) == frozenset()
    assert _parse_admin_ids("") == frozenset()
    assert _parse_admin_ids("111, 222 ,333") == frozenset({111, 222, 333})


async def _exercise_backup_restore() -> None:
    """проверяет замену базы и распознавание файла SQLite"""
    base = Path(tempfile.mkdtemp())
    path_a = base / "a.sqlite3"
    path_b = base / "b.sqlite3"

    storage_a = SQLiteHistoryStorage(path_a)
    await storage_a.initialize()
    await storage_a.save_user_word(1, "из_a")

    storage_b = SQLiteHistoryStorage(path_b)
    await storage_b.initialize()
    await storage_b.save_user_word(2, "из_b")

    assert _is_sqlite_file(path_b) is True
    not_sqlite = base / "x.bin"
    not_sqlite.write_bytes(b"not a database")
    assert _is_sqlite_file(not_sqlite) is False

    await storage_a.replace_database(path_b)
    assert await storage_a.count_user_words(2) == 1
    assert await storage_a.count_user_words(1) == 0


def test_backup_restore() -> None:
    """прогоняет проверки бэкапа/восстановления"""
    asyncio.run(_exercise_backup_restore())
