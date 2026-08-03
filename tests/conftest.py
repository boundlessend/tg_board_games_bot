"""общие фикстуры: путь к данным и временное хранилище"""

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import SQLiteHistoryStorage  # noqa: E402
from services.bunker import BunkerContent, load_bunker_content  # noqa: E402
from services.content import (  # noqa: E402
    DangerousWordsContent,
    WordGame,
    load_dangerous_words_content,
    load_word_games,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="session")
def data_dir() -> Path:
    """каталог с игровым контентом"""
    return DATA_DIR


@pytest.fixture(scope="session")
def word_games() -> list[WordGame]:
    """словесные игры, загруженные из data"""
    return load_word_games(DATA_DIR)


@pytest.fixture(scope="session")
def dangerous_content() -> DangerousWordsContent:
    """контент «опасных слов»"""
    return load_dangerous_words_content(DATA_DIR)


@pytest.fixture(scope="session")
def bunker_content() -> BunkerContent:
    """контент игры бункер"""
    return load_bunker_content(DATA_DIR)


@pytest_asyncio.fixture
async def storage(tmp_path: Path) -> AsyncIterator[SQLiteHistoryStorage]:
    """готовое к работе хранилище во временном каталоге"""
    instance = SQLiteHistoryStorage(tmp_path / "db" / "test.sqlite3")
    await instance.initialize()
    yield instance
    await instance.dispose()
