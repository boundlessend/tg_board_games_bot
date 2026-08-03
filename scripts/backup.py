"""периодические снимки базы с ротацией

запускается отдельным контейнером рядом с ботом: раз в интервал делает
VACUUM INTO в каталог бэкапов и оставляет только последние снимки
"""

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import DatabaseError, SQLiteHistoryStorage  # noqa: E402
from logging_setup import configure_logging  # noqa: E402

logger = logging.getLogger("backup")

DEFAULT_INTERVAL_SECONDS = 86_400
DEFAULT_KEEP = 7


async def run_backups(
    database_path: Path, backup_dir: Path, interval: int, keep: int
) -> None:
    """бесконечно снимает бэкапы с заданным интервалом"""
    storage = SQLiteHistoryStorage(database_path)
    backup_dir.mkdir(parents=True, exist_ok=True)
    try:
        while True:
            await make_backup(storage, backup_dir, keep)
            await asyncio.sleep(interval)
    finally:
        await storage.dispose()


async def make_backup(
    storage: SQLiteHistoryStorage, backup_dir: Path, keep: int
) -> Path | None:
    """снимает один бэкап и удаляет лишние, возвращает путь снимка"""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"bot-{stamp}.sqlite3"
    try:
        await storage.backup_snapshot(destination)
    except DatabaseError:
        logger.exception("backup_failed", extra={"destination": str(destination)})
        return None

    logger.info("backup_created", extra={"destination": str(destination)})
    prune_backups(backup_dir, keep)
    return destination


def prune_backups(backup_dir: Path, keep: int) -> list[Path]:
    """оставляет только keep свежих снимков, возвращает удалённые"""
    snapshots = sorted(backup_dir.glob("bot-*.sqlite3"))
    stale = snapshots[:-keep] if keep > 0 else snapshots
    for path in stale:
        path.unlink(missing_ok=True)
        logger.info("backup_pruned", extra={"path": str(path)})
    return stale


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """разбирает аргументы командной строки"""
    parser = argparse.ArgumentParser(description="периодические бэкапы базы бота")
    parser.add_argument("--database", type=Path, default=Path("/db/bot.sqlite3"))
    parser.add_argument("--backup-dir", type=Path, default=Path("/backups"))
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    return parser.parse_args(argv)


def main(argv: list[str]) -> None:
    """точка входа сервиса бэкапов"""
    configure_logging(logging.INFO)
    args = _parse_args(argv)
    asyncio.run(run_backups(args.database, args.backup_dir, args.interval, args.keep))


if __name__ == "__main__":
    main(sys.argv[1:])
