"""проверки хранилища: выдачи без повторов, контент, аналитика, бэкапы"""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from database import (
    DatabaseError,
    SQLiteHistoryStorage,
    iso_days_ago,
    snapshot_has_core_tables,
)
from exceptions import DuplicateHistoryItemError
from services.content import EmptyPoolError, WordGame
from services.picking import identity, select_unique_item

USER = 5


async def _select(storage: SQLiteHistoryStorage, pool: list[str]) -> tuple[str, int]:
    """выбирает слово игры «tg» через общий механизм без повторов"""

    async def get_seen(user_id: int) -> set[str]:
        return await storage.get_user_game_words(user_id, "tg")

    async def save_seen(user_id: int, word: str) -> None:
        await storage.save_user_game_word(user_id, "tg", word)

    return await select_unique_item(
        items=pool,
        get_item_id=identity,
        get_seen_ids=get_seen,
        save_seen_id=save_seen,
        telegram_id=USER,
    )


async def test_creates_parent_directory(tmp_path: Path) -> None:
    """каталог базы создаётся при инициализации"""
    path = tmp_path / "nested" / "bot.sqlite3"
    storage = SQLiteHistoryStorage(path)
    await storage.initialize()
    assert path.parent.exists()
    await storage.dispose()


async def test_words_are_issued_without_repeats(
    storage: SQLiteHistoryStorage,
) -> None:
    """пул выдаётся без повторов, счётчик растёт, затем пул кончается"""
    first, first_count = await _select(storage, ["a", "b"])
    second, second_count = await _select(storage, ["a", "b"])
    assert {first, second} == {"a", "b"}
    assert (first_count, second_count) == (1, 2)

    with pytest.raises(EmptyPoolError):
        await _select(storage, ["a", "b"])

    await storage.reset_user_game_words(USER, "tg")
    _, restarted = await _select(storage, ["a", "b"])
    assert restarted == 1


async def test_custom_content_roundtrip(storage: SQLiteHistoryStorage) -> None:
    """пользовательский контент добавляется, читается и удаляется"""
    await storage.add_custom_word("crocodile", "кастом")
    assert "кастом" in await storage.get_custom_words("crocodile")
    with pytest.raises(DuplicateHistoryItemError):
        await storage.add_custom_word("crocodile", "кастом")
    assert await storage.delete_custom_word("crocodile", "кастом") is True
    assert await storage.delete_custom_word("crocodile", "кастом") is False

    await storage.add_custom_curse("заголовок", "описание")
    curses = await storage.get_custom_curses()
    assert curses[0].id.startswith("cc_")
    assert await storage.delete_custom_curse(int(curses[0].id.removeprefix("cc_")))

    await storage.add_custom_boss("имя", "описание")
    bosses = await storage.get_custom_bosses()
    assert bosses[0].id.startswith("cb_")
    assert await storage.delete_custom_boss(int(bosses[0].id.removeprefix("cb_")))


async def test_bulk_import_skips_duplicates(storage: SQLiteHistoryStorage) -> None:
    """массовый импорт добавляет только новые слова"""
    added = await storage.add_custom_words_bulk("alias", ["раз", "два", "раз"])
    assert added == 2
    assert await storage.add_custom_words_bulk("alias", ["два", "три"]) == 1
    assert sorted(await storage.get_custom_words("alias")) == ["два", "раз", "три"]
    assert await storage.add_custom_words_bulk("alias", []) == 0


async def test_settings_favorites_and_last_word(
    storage: SQLiteHistoryStorage,
) -> None:
    """настройки, последнее слово и избранное сохраняются"""
    assert await storage.get_user_auto_cycle(USER) is True
    await storage.set_user_auto_cycle(USER, False)
    assert await storage.get_user_auto_cycle(USER) is False

    await storage.set_last_word(USER, "слово")
    assert await storage.get_last_word(USER) == "слово"
    assert await storage.get_last_word(404) is None

    assert await storage.add_favorite(USER, "слово") is True
    assert await storage.add_favorite(USER, "слово") is False
    assert await storage.get_favorites(USER) == ["слово"]
    await storage.clear_favorites(USER)
    assert await storage.get_favorites(USER) == []


async def test_delete_user_data_removes_every_trace(
    storage: SQLiteHistoryStorage,
) -> None:
    """forgetme стирает историю, избранное и настройки пользователя"""
    await storage.save_user_word(USER, "слово")
    await storage.save_user_curse(USER, "c1")
    await storage.save_user_boss(USER, "b1")
    await storage.save_user_game_word(USER, "alias", "игровое")
    await storage.add_favorite(USER, "любимое")
    await storage.set_user_auto_cycle(USER, False)
    await storage.set_last_word(USER, "последнее")

    removed = await storage.delete_user_data(USER)
    assert removed == 7
    assert await storage.count_user_words(USER) == 0
    assert await storage.get_favorites(USER) == []
    assert await storage.get_last_word(USER) is None
    assert await storage.get_user_auto_cycle(USER) is True
    assert await storage.get_user_game_words(USER, "alias") == set()


async def test_summary_and_activity(storage: SQLiteHistoryStorage) -> None:
    """сводка считает все виды выдач, активность бьётся по дням"""
    await storage.save_user_word(1, "альфа")
    await storage.save_user_word(2, "альфа")
    await storage.save_user_curse(1, "c1")
    await storage.save_user_boss(1, "b1")
    await storage.save_user_game_word(1, "alias", "игровое")

    totals = await storage.get_summary_totals()
    assert totals.users == 2
    assert totals.dangerous_words == 2
    assert (totals.curses, totals.bosses, totals.game_words) == (1, 1, 1)

    assert (await storage.get_top_words(10))[0] == ("альфа", 2)
    assert await storage.count_issuances_since(iso_days_ago(1)) == 5
    assert await storage.count_active_users_since(iso_days_ago(1)) == 2
    by_day = await storage.issuances_by_day(iso_days_ago(1))
    assert by_day and by_day[-1][1] == 5
    assert await storage.count_issuances_since("9999-01-01T00:00:00+00:00") == 0


async def test_sessions_are_saved_and_expire(
    storage: SQLiteHistoryStorage,
) -> None:
    """снапшот сессии сохраняется, а протухший убирается по TTL"""
    await storage.save_session("group", "-100", '{"a": 1}')
    assert await storage.load_session_scope("group") == {"-100": '{"a": 1}'}

    assert await storage.delete_stale_sessions(iso_days_ago(1)) == 0
    assert await storage.delete_stale_sessions("9999-01-01T00:00:00+00:00") == 1
    assert await storage.load_session_scope("group") == {}

    await storage.replace_session_scope("bunker", {"a": "{}", "b": "{}"})
    assert set(await storage.load_session_scope("bunker")) == {"a", "b"}
    await storage.delete_session("bunker", "a")
    assert set(await storage.load_session_scope("bunker")) == {"b"}


async def test_backup_snapshot_and_restore(tmp_path: Path) -> None:
    """снимок базы переносит данные и проверяется по схеме"""
    source = SQLiteHistoryStorage(tmp_path / "a.sqlite3")
    await source.initialize()
    await source.save_user_word(1, "из_a")

    donor = SQLiteHistoryStorage(tmp_path / "b.sqlite3")
    await donor.initialize()
    await donor.save_user_word(2, "из_b")
    # живая WAL-база не копируется файлом - источник только снимок VACUUM INTO
    snapshot = tmp_path / "snapshot.sqlite3"
    await donor.backup_snapshot(snapshot)

    assert snapshot_has_core_tables(snapshot) is True
    foreign = tmp_path / "foreign.sqlite3"
    foreign.write_bytes(b"not a database")
    assert snapshot_has_core_tables(foreign) is False

    await source.replace_database(snapshot)
    assert await source.count_user_words(2) == 1
    assert await source.count_user_words(1) == 0
    await source.dispose()
    await donor.dispose()


async def test_database_error_is_explicit(tmp_path: Path) -> None:
    """сбой базы поднимается как DatabaseError, а не как ошибка драйвера"""
    storage = SQLiteHistoryStorage(tmp_path / "broken.sqlite3")
    with pytest.raises(DatabaseError):
        await storage.count_user_words(1)


def test_word_game_is_immutable(word_games: list[WordGame]) -> None:
    """модель игры неизменяема, чтобы её нельзя было испортить в рантайме"""
    with pytest.raises(FrozenInstanceError):
        word_games[0].title = "другое"  # type: ignore[misc]
