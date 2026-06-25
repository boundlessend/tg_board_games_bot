from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """ошибка конфигурации приложения"""

    pass


@dataclass(frozen=True)
class BotConfig:
    """настройки telegram-бота"""

    bot_token: str
    database_path: Path
    data_dir: Path
    admin_ids: frozenset[int]


def load_config() -> BotConfig:
    """загружает конфигурацию из окружения"""
    project_dir = Path(__file__).resolve().parent
    load_dotenv(project_dir / ".env")

    bot_token = os.getenv("BOT_TOKEN")
    if bot_token is None or bot_token.strip() == "":
        raise ConfigError(
            "BOT_TOKEN не задан. Создай .env на основе .env.example."
        )

    return BotConfig(
        bot_token=bot_token.strip(),
        database_path=_resolve_database_path(
            os.getenv("DATABASE_PATH"), project_dir
        ),
        data_dir=project_dir / "data",
        admin_ids=_parse_admin_ids(os.getenv("ADMIN_IDS")),
    )


def _resolve_database_path(
    raw_database_path: str | None, project_dir: Path
) -> Path:
    """определяет путь к файлу базы из окружения или по умолчанию"""
    if raw_database_path is None or raw_database_path.strip() == "":
        return project_dir / "bot.sqlite3"
    return Path(raw_database_path.strip())


def _parse_admin_ids(raw_admin_ids: str | None) -> frozenset[int]:
    """разбирает список telegram id администраторов из строки окружения"""
    if raw_admin_ids is None or raw_admin_ids.strip() == "":
        return frozenset()

    admin_ids: set[int] = set()
    for chunk in raw_admin_ids.split(","):
        value = chunk.strip()
        if value == "":
            continue
        try:
            admin_ids.add(int(value))
        except ValueError as error:
            raise ConfigError(
                f"ADMIN_IDS содержит нечисловое значение: {value!r}."
            ) from error

    return frozenset(admin_ids)
