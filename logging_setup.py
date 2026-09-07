"""настройка логирования со структурными полями из extra"""

import json
import logging

_BASE_RECORD_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
)
_EXTRA_FIELDS_TO_SKIP = frozenset({"message", "asctime", "taskName"})


class StructuredFormatter(logging.Formatter):
    """сериализует запись лога целиком в один json-объект

    гибрид «текст плюс json в хвосте» коллектор логов не разберёт, поэтому
    базовые поля и переданные через extra пишутся одним объектом
    """

    def format(self, record: logging.LogRecord) -> str:
        """собирает json-строку из полей записи"""
        payload: dict[str, object] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in _BASE_RECORD_FIELDS and key not in _EXTRA_FIELDS_TO_SKIP
            }
        )
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int) -> None:
    """включает логирование со структурными полями"""
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
