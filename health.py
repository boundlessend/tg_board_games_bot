"""отметка живости процесса для docker healthcheck

polling-бот может «зависнуть», оставаясь живым процессом: docker сам этого
не заметит. Бот регулярно обновляет файл-отметку, а healthcheck проверяет
её свежесть и перезапускает контейнер, если отметка протухла
"""

import asyncio
import logging
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

HEARTBEAT_PATH = Path("/tmp/bot-heartbeat")
HEARTBEAT_INTERVAL_SECONDS = 30
STALE_AFTER_SECONDS = 120


def touch_heartbeat(path: Path) -> None:
    """обновляет отметку живости"""
    path.write_text(str(time.time()), encoding="utf-8")


def is_alive(path: Path, stale_after: float) -> bool:
    """проверяет, что отметка свежая"""
    try:
        stamp = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return time.time() - stamp < stale_after


async def heartbeat_loop(
    path: Path = HEARTBEAT_PATH, interval: int = HEARTBEAT_INTERVAL_SECONDS
) -> None:
    """обновляет отметку, пока работает бот"""
    while True:
        try:
            touch_heartbeat(path)
        except OSError:
            logger.warning("heartbeat_write_failed", extra={"path": str(path)})
        await asyncio.sleep(interval)


if __name__ == "__main__":
    sys.exit(0 if is_alive(HEARTBEAT_PATH, STALE_AFTER_SECONDS) else 1)
