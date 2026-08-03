"""эксплуатационная обвязка: бэкапы с ротацией, heartbeat, формат логов"""

import logging
import time
from pathlib import Path

from database import SQLiteHistoryStorage
from health import is_alive, touch_heartbeat
from logging_setup import StructuredFormatter
from scripts.backup import make_backup, prune_backups


async def test_backup_creates_snapshot_and_rotates(tmp_path: Path) -> None:
    """снимок создаётся, лишние удаляются, свежие остаются"""
    storage = SQLiteHistoryStorage(tmp_path / "bot.sqlite3")
    await storage.initialize()
    await storage.save_user_word(1, "слово")

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    snapshot = await make_backup(storage, backup_dir, keep=3)
    assert snapshot is not None and snapshot.exists()

    restored = SQLiteHistoryStorage(snapshot)
    assert await restored.count_user_words(1) == 1
    await restored.dispose()
    await storage.dispose()


def test_prune_keeps_only_recent_snapshots(tmp_path: Path) -> None:
    """ротация оставляет заданное число последних снимков"""
    for index in range(5):
        (tmp_path / f"bot-2026010{index}T000000Z.sqlite3").write_bytes(b"x")

    removed = prune_backups(tmp_path, keep=2)
    assert len(removed) == 3
    remaining = sorted(path.name for path in tmp_path.glob("bot-*.sqlite3"))
    assert remaining == [
        "bot-20260103T000000Z.sqlite3",
        "bot-20260104T000000Z.sqlite3",
    ]


def test_prune_with_zero_keep_clears_everything(tmp_path: Path) -> None:
    """keep=0 стирает все снимки, а не оставляет последний"""
    (tmp_path / "bot-20260101T000000Z.sqlite3").write_bytes(b"x")
    assert len(prune_backups(tmp_path, keep=0)) == 1
    assert list(tmp_path.glob("bot-*.sqlite3")) == []


def test_heartbeat_detects_stale_process(tmp_path: Path) -> None:
    """протухшая отметка живости распознаётся как сбой"""
    path = tmp_path / "heartbeat"
    assert is_alive(path, stale_after=60) is False

    touch_heartbeat(path)
    assert is_alive(path, stale_after=60) is True
    assert is_alive(path, stale_after=0.0) is False

    path.write_text("не число", encoding="utf-8")
    assert is_alive(path, stale_after=60) is False


def test_structured_formatter_keeps_extra_fields() -> None:
    """поля из extra попадают в строку лога"""
    formatter = StructuredFormatter("%(levelname)s %(message)s")
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="database_error",
        args=None,
        exc_info=None,
    )
    record.telegram_id = 42
    record.action = "wg_word"

    line = formatter.format(record)
    assert "database_error" in line
    assert '"telegram_id": 42' in line
    assert '"action": "wg_word"' in line


def test_structured_formatter_without_extra_stays_plain() -> None:
    """без extra строка лога не обрастает лишним хвостом"""
    formatter = StructuredFormatter("%(message)s")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="started",
        args=None,
        exc_info=None,
    )
    assert formatter.format(record) == "started"


def test_heartbeat_timestamp_moves_forward(tmp_path: Path) -> None:
    """повторная отметка обновляет время"""
    path = tmp_path / "heartbeat"
    touch_heartbeat(path)
    first = float(path.read_text(encoding="utf-8"))
    time.sleep(0.01)
    touch_heartbeat(path)
    assert float(path.read_text(encoding="utf-8")) > first
