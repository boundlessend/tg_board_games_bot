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
        database_path=project_dir / "bot.sqlite3",
        data_dir=project_dir / "data",
    )
