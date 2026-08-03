"""настройка логирования со структурными полями из extra"""

import json
import logging

_BASE_RECORD_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
)
_EXTRA_FIELDS_TO_SKIP = frozenset({"message", "asctime", "taskName"})


class StructuredFormatter(logging.Formatter):
    """дописывает поля из extra в конец строки лога

    без этого logger.exception(..., extra={"telegram_id": ...}) теряет
    контекст: стандартный форматтер такие поля просто не печатает
    """

    def format(self, record: logging.LogRecord) -> str:
        """форматирует запись, добавляя её пользовательские поля"""
        base = super().format(record)
        fields = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _BASE_RECORD_FIELDS and key not in _EXTRA_FIELDS_TO_SKIP
        }
        if not fields:
            return base
        return f"{base} {json.dumps(fields, ensure_ascii=False, default=str)}"


def configure_logging(level: int) -> None:
    """включает логирование со структурными полями"""
    handler = logging.StreamHandler()
    handler.setFormatter(
        StructuredFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logging.basicConfig(level=level, handlers=[handler], force=True)
