"""эксплуатационная обвязка: бэкапы с ротацией, heartbeat, логи, рантайм"""

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import pytest
from aiogram import Dispatcher, Router
from aiogram.types import Chat, Message, Update, User

from bot import ERROR_TEXT, register_error_handler
from constants import TELEGRAM_MESSAGE_LIMIT
from database import SQLiteHistoryStorage
from handlers.common import ChatLocks, split_report
from health import is_alive, touch_heartbeat
from logging_setup import StructuredFormatter
from scripts.backup import make_backup, prune_backups
from tests.fake_bot import RecordingSession, make_bot

USER = 91


async def test_backup_creates_snapshot_and_rotates(tmp_path: Path) -> None:
    """снимок создаётся, лишние удаляются, свежие остаются"""
    storage = SQLiteHistoryStorage(tmp_path / "bot.sqlite3")
    await storage.initialize()
    await storage.save_user_game_word(1, "alias", "слово")

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    # снимки прошлых прогонов: имя со штампом времени сортируется по возрасту
    old_names = [f"bot-2020010{index}T000000Z.sqlite3" for index in range(1, 4)]
    for name in old_names:
        (backup_dir / name).write_bytes(b"x")

    snapshot = await make_backup(storage, backup_dir, keep=3)
    assert snapshot is not None and snapshot.exists()

    remaining = sorted(path.name for path in backup_dir.glob("bot-*.sqlite3"))
    assert remaining == sorted(old_names[1:] + [snapshot.name])

    restored = SQLiteHistoryStorage(snapshot)
    assert await restored.get_user_game_words(1, "alias") == {"слово"}
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


def test_prune_rejects_non_positive_keep(tmp_path: Path) -> None:
    """keep=0 это ошибка аргумента, а не команда стереть все снимки"""
    snapshot = tmp_path / "bot-20260101T000000Z.sqlite3"
    snapshot.write_bytes(b"x")

    with pytest.raises(ValueError):
        prune_backups(tmp_path, keep=0)

    assert snapshot.exists()


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
    formatter = StructuredFormatter()
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

    payload = json.loads(formatter.format(record))
    assert payload["message"] == "database_error"
    assert payload["telegram_id"] == 42
    assert payload["action"] == "wg_word"


def test_structured_formatter_writes_single_json_object() -> None:
    """запись без extra остаётся разбираемым json с базовыми полями"""
    formatter = StructuredFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="started",
        args=None,
        exc_info=None,
    )
    payload = json.loads(formatter.format(record))
    assert payload["message"] == "started"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"


def test_heartbeat_timestamp_moves_forward(tmp_path: Path) -> None:
    """повторная отметка обновляет время"""
    path = tmp_path / "heartbeat"
    touch_heartbeat(path)
    first = float(path.read_text(encoding="utf-8"))
    time.sleep(0.01)
    touch_heartbeat(path)
    assert float(path.read_text(encoding="utf-8")) > first


async def test_chat_locks_serialize_one_chat_only() -> None:
    """события одного чата идут по очереди, разные чаты друг друга не ждут"""
    locks = ChatLocks()
    trace: list[str] = []

    async def work(chat_id: int, tag: str) -> None:
        async with locks.hold(chat_id):
            trace.append(f"{tag}-вошёл")
            await asyncio.sleep(0)
            trace.append(f"{tag}-вышел")

    await asyncio.gather(work(-100, "первый"), work(-100, "второй"))
    assert trace == ["первый-вошёл", "первый-вышел", "второй-вошёл", "второй-вышел"]
    assert len(locks) == 0

    trace.clear()
    await asyncio.gather(work(-100, "первый"), work(-200, "второй"))
    assert trace[:2] == ["первый-вошёл", "второй-вошёл"]
    assert len(locks) == 0


def test_split_report_respects_telegram_limit() -> None:
    """строка ровно в лимит цела, длиннее - режется, короткие склеиваются"""
    exact = "я" * TELEGRAM_MESSAGE_LIMIT
    assert split_report(exact) == [exact]

    long_line = "я" * (TELEGRAM_MESSAGE_LIMIT + 10)
    pieces = split_report(long_line)
    assert [len(piece) for piece in pieces] == [TELEGRAM_MESSAGE_LIMIT, 10]
    assert "".join(pieces) == long_line

    assert split_report("раз\nдва\nтри") == ["раз\nдва\nтри"]

    half = "я" * (TELEGRAM_MESSAGE_LIMIT // 2)
    chunks = split_report(f"{half}\n{half}")
    assert len(chunks) == 2
    assert all(len(chunk) <= TELEGRAM_MESSAGE_LIMIT for chunk in chunks)


async def test_error_handler_answers_the_user() -> None:
    """падение хендлера превращается в понятный ответ, а не в тишину"""
    recording = RecordingSession()
    bot = make_bot(recording)
    dispatcher = Dispatcher()
    router = Router()

    @router.message()
    async def handle_and_fail(message: Message) -> None:
        """хендлер, который падает при любом сообщении"""
        raise RuntimeError("сбой внутри хендлера")

    dispatcher.include_router(router)
    register_error_handler(dispatcher, bot, frozenset())

    message = Message.model_construct(
        message_id=1,
        date=datetime(2026, 1, 1),
        chat=Chat.model_construct(id=USER, type="private"),
        from_user=User.model_construct(id=USER, is_bot=False, first_name="Ю"),
        text="/start",
    )
    await dispatcher.feed_update(
        bot, Update.model_construct(update_id=1, message=message)
    )

    assert recording.sent_to(USER) == [ERROR_TEXT]
