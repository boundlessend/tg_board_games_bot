"""отметка живости процесса и сторож зависшего polling

polling-бот может «зависнуть», оставаясь живым процессом. Отметку живости
двигает сама работа диспетчера, а сторож внутри процесса шлёт себе SIGTERM,
когда отметка протухла: контейнер поднимает заново политика restart. Docker
на статус healthcheck не реагирует, поэтому HEALTHCHECK только сообщает
состояние наружу
"""

import asyncio
import logging
import signal
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

HEARTBEAT_PATH = Path("/tmp/bot-heartbeat")
HEARTBEAT_INTERVAL_SECONDS = 30
WATCHDOG_INTERVAL_SECONDS = 30
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
    probe: Callable[[], Awaitable[bool]],
    path: Path = HEARTBEAT_PATH,
    interval: int = HEARTBEAT_INTERVAL_SECONDS,
) -> None:
    """обновляет отметку, пока telegram отвечает боту

    основную отметку ставит middleware диспетчера по факту обработки
    апдейтов, но в тихих чатах апдейтов может не быть часами, поэтому
    живость подтверждается ещё и запросом к telegram
    """
    while True:
        if await probe():
            try:
                touch_heartbeat(path)
            except OSError:
                logger.warning("heartbeat_write_failed", extra={"path": str(path)})
        await asyncio.sleep(interval)


async def watchdog_loop(
    path: Path = HEARTBEAT_PATH,
    interval: int = WATCHDOG_INTERVAL_SECONDS,
    stale_after: float = STALE_AFTER_SECONDS,
) -> None:
    """останавливает процесс, когда отметка живости протухла"""
    while True:
        await asyncio.sleep(interval)
        if is_alive(path, stale_after):
            continue
        logger.error(
            "heartbeat_stale", extra={"path": str(path), "stale_after": stale_after}
        )
        signal.raise_signal(signal.SIGTERM)


if __name__ == "__main__":
    sys.exit(0 if is_alive(HEARTBEAT_PATH, STALE_AFTER_SECONDS) else 1)
