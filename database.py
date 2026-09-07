import json
import logging
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    Column,
    Index,
    Integer,
    MetaData,
    String,
    Subquery,
    Table,
    UniqueConstraint,
    delete,
    func,
    insert,
    select,
    text,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    create_async_engine,
)

from exceptions import DuplicateHistoryItemError
from services.content import Boss, Curse

logger = logging.getLogger(__name__)

USER_GAME_WORDS_TABLE_NAME = "user_game_words"

# разобранный json снапшота сессии: вложенность произвольная, поэтому тип
# рекурсивный
type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)

# версия схемы в PRAGMA user_version: растёт при каждом несовместимом
# изменении, чтобы бот не открыл базу, собранную более новой версией
SCHEMA_VERSION = 1

metadata = MetaData()

# индекс по issued_at нужен аналитике: выдачи за период и активность по дням
# фильтруют по нему, а без индекса это полное сканирование таблицы истории
user_game_words_table = Table(
    USER_GAME_WORDS_TABLE_NAME,
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("telegram_id", BigInteger, nullable=False),
    Column("game_id", String, nullable=False),
    Column("word", String, nullable=False),
    Column("issued_at", String, nullable=True),
    UniqueConstraint("telegram_id", "game_id", "word"),
    Index("ix_user_game_words_issued_at", "issued_at"),
)

custom_words_table = Table(
    "custom_words",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("game_id", String, nullable=False),
    Column("word", String, nullable=False),
    UniqueConstraint("game_id", "word"),
)

# sqlite_autoincrement не даёт переиспользовать id удалённой записи: иначе
# новое проклятие получало бы cc_N, уже отмеченный выданным в идущей партии
custom_curses_table = Table(
    "custom_curses",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("title", String, nullable=False),
    Column("description", String, nullable=False),
    sqlite_autoincrement=True,
)

custom_bosses_table = Table(
    "custom_bosses",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String, nullable=False),
    Column("description", String, nullable=False),
    sqlite_autoincrement=True,
)

user_settings_table = Table(
    "user_settings",
    metadata,
    Column("telegram_id", BigInteger, primary_key=True),
    Column("auto_cycle", Integer, nullable=False),
)

user_last_word_table = Table(
    "user_last_word",
    metadata,
    Column("telegram_id", BigInteger, primary_key=True),
    Column("word", String, nullable=False),
)

favorites_table = Table(
    "favorites",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("telegram_id", BigInteger, nullable=False),
    Column("word", String, nullable=False),
    UniqueConstraint("telegram_id", "word"),
)

session_state_table = Table(
    "session_state",
    metadata,
    Column("scope", String, primary_key=True),
    Column("key", String, primary_key=True),
    Column("data", String, nullable=False),
    Column("updated_at", String, nullable=True),
)


_HISTORY_TABLE_NAMES: tuple[str, ...] = (USER_GAME_WORDS_TABLE_NAME,)

# таблицы, которые целиком принадлежат одному пользователю: чистятся по /forgetme
_USER_OWNED_TABLE_NAMES: tuple[str, ...] = (
    *_HISTORY_TABLE_NAMES,
    "favorites",
    "user_settings",
    "user_last_word",
)


def _now_iso() -> str:
    """возвращает текущее время в ISO-формате UTC"""
    return datetime.now(UTC).isoformat()


def _normalize_word(word: str) -> str:
    """приводит слово пула к каноническому виду для сравнения без повторов"""
    return word.strip().lower()


def iso_days_ago(days: int) -> str:
    """возвращает ISO-метку времени days дней назад (UTC)"""
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _issuances_subquery() -> Subquery:
    """строит выдачи как (telegram_id, issued_at) для аналитики"""
    return select(
        user_game_words_table.c.telegram_id.label("telegram_id"),
        user_game_words_table.c.issued_at.label("issued_at"),
    ).subquery()


class DatabaseError(RuntimeError):
    """ошибка работы с базой данных"""

    pass


def _parse_snapshot_or_none(scope: str, key: str, data: str) -> JsonValue | None:
    """разбирает json снапшота сессии, возвращая None для повреждённой строки

    повреждённый снапшот не восстановится и при старте бота, поэтому он не
    ошибка вызывающего, а мусор, который вызывающий волен убрать
    """
    try:
        snapshot: JsonValue = json.loads(data)
    except json.JSONDecodeError:
        logger.warning(
            "session_snapshot_unreadable", extra={"scope": scope, "key": key}
        )
        return None

    return snapshot


def _snapshot_mentions_user(value: JsonValue, telegram_id: int) -> bool:
    """ищет telegram_id в разобранном снапшоте сессии

    id игрока лежит и в значениях, и в ключах словарей, где json хранит его
    строкой, поэтому сравниваем оба представления
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value == telegram_id
    if isinstance(value, str):
        return value == str(telegram_id)
    if isinstance(value, list):
        return any(_snapshot_mentions_user(item, telegram_id) for item in value)
    if isinstance(value, dict):
        return any(
            key == str(telegram_id) or _snapshot_mentions_user(item, telegram_id)
            for key, item in value.items()
        )
    return False


@dataclass(frozen=True)
class SummaryTotals:
    """агрегаты админской сводки: история ведётся только по словесным играм"""

    users: int
    game_words: int


def snapshot_has_core_tables(path: Path) -> bool:
    """проверяет, что файл базы содержит таблицы истории выдач

    сигнатуры SQLite мало: валидный, но чужой файл заменил бы рабочую базу
    и оставил бота без данных
    """
    try:
        # closing обязателен: контекст sqlite3 закрывает транзакцию, но не
        # соединение, и дескриптор файла жил бы до сборки мусора
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
    except sqlite3.Error:
        return False
    present = {str(row[0]) for row in rows}
    return set(_HISTORY_TABLE_NAMES).issubset(present)


def _create_engine(database_path: Path) -> AsyncEngine:
    """создаёт async-движок sqlite с запасом ожидания блокировок"""
    return create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        connect_args={"timeout": 30},
    )


class SQLiteHistoryStorage:
    def __init__(self, database_path: Path) -> None:
        """создаёт хранилище истории выдач"""
        self._database_path: Path = database_path
        self._engine: AsyncEngine = _create_engine(database_path)

    async def dispose(self) -> None:
        """закрывает пул соединений при остановке бота"""
        await self._engine.dispose()

    async def backup_snapshot(self, destination: Path) -> None:
        """создаёт консистентный снимок базы в файле destination (VACUUM INTO)"""
        try:
            async with self._engine.connect() as connection:
                autocommit = await connection.execution_options(
                    isolation_level="AUTOCOMMIT"
                )
                await autocommit.exec_driver_sql("VACUUM INTO ?", (str(destination),))
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось создать снимок базы в {destination}."
            ) from error

    async def replace_database(self, source_path: Path) -> None:
        """заменяет файл базы и пересоздаёт подключение"""
        try:
            if self._database_path.exists():
                # ponytail: один .bak, последний снимок перед заменой
                backup_path = self._database_path.with_name(
                    self._database_path.name + ".bak"
                )
                backup_path.unlink(missing_ok=True)
                await self.backup_snapshot(backup_path)
            await self._engine.dispose()
            # хвосты WAL старой базы не должны примешаться к новой
            for suffix in ("-wal", "-shm"):
                Path(str(self._database_path) + suffix).unlink(missing_ok=True)
            shutil.copyfile(source_path, self._database_path)
            self._engine = _create_engine(self._database_path)
        except (OSError, SQLAlchemyError) as error:
            raise DatabaseError("Не удалось заменить файл базы.") from error
        await self.initialize()

    async def initialize(self) -> None:
        """создаёт каталог, таблицы, недостающие колонки и включает WAL"""
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with self._engine.connect() as connection:
                autocommit = await connection.execution_options(
                    isolation_level="AUTOCOMMIT"
                )
                # WAL записывается в файл базы и переживает переподключения
                await autocommit.exec_driver_sql("PRAGMA journal_mode=WAL")
            async with self._engine.begin() as connection:
                await connection.run_sync(metadata.create_all)
                for table_name in _HISTORY_TABLE_NAMES:
                    await self._ensure_column(connection, table_name, "issued_at")
                await self._ensure_column(connection, "session_state", "updated_at")
                await self._ensure_indexes(connection)
                await self._stamp_schema_version(connection)
        except SQLAlchemyError as error:
            raise DatabaseError("Не удалось инициализировать SQLite-базу.") from error

    async def _ensure_column(
        self, connection: AsyncConnection, table_name: str, column_name: str
    ) -> None:
        """добавляет текстовую колонку в существующую таблицу, если её нет"""
        result = await connection.execute(text(f"PRAGMA table_info({table_name})"))
        columns = {row[1] for row in result.fetchall()}
        if column_name not in columns:
            await connection.execute(
                text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} TEXT")
            )

    async def _ensure_indexes(self, connection: AsyncConnection) -> None:
        """досоздаёт индексы на таблицах, созданных прошлыми версиями схемы

        create_all пропускает существующую таблицу целиком, вместе с её
        индексами, поэтому база, пережившая добавление индекса, осталась бы
        без него навсегда
        """
        for table in metadata.tables.values():
            for index in table.indexes:
                await connection.run_sync(index.create, checkfirst=True)

    async def _stamp_schema_version(self, connection: AsyncConnection) -> None:
        """проставляет версию схемы, отказываясь работать с базой новее себя

        без отметки нельзя отличить базу этой версии от снимка, снятого
        будущей версией бота: она пришла бы через /restore и молча потеряла
        часть данных
        """
        result = await connection.execute(text("PRAGMA user_version"))
        current = int(result.scalar_one())
        if current > SCHEMA_VERSION:
            raise DatabaseError(
                f"База собрана схемой версии {current}, "
                f"а бот понимает только {SCHEMA_VERSION}."
            )
        if current != SCHEMA_VERSION:
            await connection.execute(text(f"PRAGMA user_version={SCHEMA_VERSION}"))

    async def get_user_game_words(self, telegram_id: int, game_id: str) -> set[str]:
        """возвращает слова словесной игры, выданные пользователю"""
        statement = select(user_game_words_table.c.word).where(
            user_game_words_table.c.telegram_id == telegram_id,
            user_game_words_table.c.game_id == game_id,
        )
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                rows = result.fetchall()
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось получить историю слов игры {game_id} для telegram_id={telegram_id}."
            ) from error

        return {str(row[0]) for row in rows}

    async def save_user_game_word(
        self, telegram_id: int, game_id: str, word: str
    ) -> None:
        """сохраняет выданное пользователю слово словесной игры"""
        statement = insert(user_game_words_table).values(
            telegram_id=telegram_id,
            game_id=game_id,
            word=word,
            issued_at=_now_iso(),
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except IntegrityError as error:
            raise DuplicateHistoryItemError(
                f"Слово уже выдано: telegram_id={telegram_id}, game_id={game_id}, word={word}."
            ) from error
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось сохранить слово игры {game_id} для telegram_id={telegram_id}."
            ) from error

    async def reset_user_game_words(self, telegram_id: int, game_id: str) -> None:
        """очищает историю слов словесной игры пользователя"""
        statement = delete(user_game_words_table).where(
            user_game_words_table.c.telegram_id == telegram_id,
            user_game_words_table.c.game_id == game_id,
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось очистить историю слов игры {game_id} для telegram_id={telegram_id}."
            ) from error

    async def add_custom_word(self, game_id: str, word: str) -> None:
        """добавляет пользовательское слово в пул игры

        слово нормализуется здесь, а не у вызывающего: UNIQUE в sqlite
        регистрозависим, и «Кот» рядом с «кот» пробил бы выдачу без повторов
        """
        statement = insert(custom_words_table).values(
            game_id=game_id, word=_normalize_word(word)
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except IntegrityError as error:
            raise DuplicateHistoryItemError(
                f"Слово уже есть в пуле игры {game_id}: {word}."
            ) from error
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось добавить слово в пул игры {game_id}."
            ) from error

    async def add_custom_words_bulk(self, game_id: str, words: list[str]) -> int:
        """добавляет слова в пул игры одной транзакцией, возвращает число новых

        дубли отбрасываются на уровне базы (on conflict do nothing), поэтому
        пак на тысячу слов стоит одну транзакцию, а не тысячу
        """
        if len(words) == 0:
            return 0
        normalized = dict.fromkeys(_normalize_word(word) for word in words)
        rows = [{"game_id": game_id, "word": word} for word in normalized]
        statement = sqlite_insert(custom_words_table).on_conflict_do_nothing(
            index_elements=[
                custom_words_table.c.game_id,
                custom_words_table.c.word,
            ]
        )
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement, rows)
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось импортировать слова в пул игры {game_id}."
            ) from error

        return result.rowcount

    async def get_custom_words(self, game_id: str) -> list[str]:
        """возвращает пользовательские слова пула игры"""
        statement = select(custom_words_table.c.word).where(
            custom_words_table.c.game_id == game_id
        )
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                rows = result.fetchall()
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось получить пользовательские слова игры {game_id}."
            ) from error

        return [str(row[0]) for row in rows]

    async def add_custom_curse(self, title: str, description: str) -> None:
        """добавляет пользовательское проклятье, отвергая полный дубль"""
        existing = select(custom_curses_table.c.id).where(
            custom_curses_table.c.title == title,
            custom_curses_table.c.description == description,
        )
        statement = insert(custom_curses_table).values(
            title=title, description=description
        )
        try:
            async with self._engine.begin() as connection:
                if (await connection.execute(existing)).first() is not None:
                    raise DuplicateHistoryItemError(
                        f"Проклятье уже добавлено: {title}."
                    )
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise DatabaseError(
                "Не удалось добавить пользовательское проклятье."
            ) from error

    async def get_custom_curses(self) -> list[Curse]:
        """возвращает пользовательские проклятья"""
        statement = select(
            custom_curses_table.c.id,
            custom_curses_table.c.title,
            custom_curses_table.c.description,
        )
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                rows = result.fetchall()
        except SQLAlchemyError as error:
            raise DatabaseError(
                "Не удалось получить пользовательские проклятья."
            ) from error

        return [
            Curse(id=f"cc_{row[0]}", title=str(row[1]), description=str(row[2]))
            for row in rows
        ]

    async def add_custom_boss(self, name: str, description: str) -> None:
        """добавляет пользовательского босса, отвергая полный дубль"""
        existing = select(custom_bosses_table.c.id).where(
            custom_bosses_table.c.name == name,
            custom_bosses_table.c.description == description,
        )
        statement = insert(custom_bosses_table).values(
            name=name, description=description
        )
        try:
            async with self._engine.begin() as connection:
                if (await connection.execute(existing)).first() is not None:
                    raise DuplicateHistoryItemError(f"Босс уже добавлен: {name}.")
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise DatabaseError(
                "Не удалось добавить пользовательского босса."
            ) from error

    async def get_custom_bosses(self) -> list[Boss]:
        """возвращает пользовательских боссов"""
        statement = select(
            custom_bosses_table.c.id,
            custom_bosses_table.c.name,
            custom_bosses_table.c.description,
        )
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                rows = result.fetchall()
        except SQLAlchemyError as error:
            raise DatabaseError(
                "Не удалось получить пользовательских боссов."
            ) from error

        return [
            Boss(id=f"cb_{row[0]}", name=str(row[1]), description=str(row[2]))
            for row in rows
        ]

    async def delete_custom_word(self, game_id: str, word: str) -> bool:
        """удаляет пользовательское слово из пула игры"""
        statement = delete(custom_words_table).where(
            custom_words_table.c.game_id == game_id,
            custom_words_table.c.word == word,
        )
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement)
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось удалить слово из пула игры {game_id}."
            ) from error

        return result.rowcount > 0

    async def delete_custom_curse(self, row_id: int) -> bool:
        """удаляет пользовательское проклятье по числовому id"""
        statement = delete(custom_curses_table).where(
            custom_curses_table.c.id == row_id
        )
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement)
        except SQLAlchemyError as error:
            raise DatabaseError(
                "Не удалось удалить пользовательское проклятье."
            ) from error

        return result.rowcount > 0

    async def delete_custom_boss(self, row_id: int) -> bool:
        """удаляет пользовательского босса по числовому id"""
        statement = delete(custom_bosses_table).where(
            custom_bosses_table.c.id == row_id
        )
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement)
        except SQLAlchemyError as error:
            raise DatabaseError(
                "Не удалось удалить пользовательского босса."
            ) from error

        return result.rowcount > 0

    async def get_user_auto_cycle(self, telegram_id: int) -> bool:
        """возвращает настройку авто-цикла словесных игр (по умолчанию вкл)"""
        statement = select(user_settings_table.c.auto_cycle).where(
            user_settings_table.c.telegram_id == telegram_id
        )
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                value = result.scalar_one_or_none()
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось получить настройки для telegram_id={telegram_id}."
            ) from error

        if value is None:
            return True
        return bool(value)

    async def set_user_auto_cycle(self, telegram_id: int, enabled: bool) -> None:
        """сохраняет настройку авто-цикла словесных игр"""
        statement = sqlite_insert(user_settings_table).values(
            telegram_id=telegram_id, auto_cycle=int(enabled)
        )
        statement = statement.on_conflict_do_update(
            index_elements=[user_settings_table.c.telegram_id],
            set_={"auto_cycle": int(enabled)},
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось сохранить настройки для telegram_id={telegram_id}."
            ) from error

    async def set_last_word(self, telegram_id: int, word: str) -> None:
        """запоминает последнее выданное пользователю слово"""
        statement = sqlite_insert(user_last_word_table).values(
            telegram_id=telegram_id, word=word
        )
        statement = statement.on_conflict_do_update(
            index_elements=[user_last_word_table.c.telegram_id],
            set_={"word": word},
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось сохранить последнее слово для telegram_id={telegram_id}."
            ) from error

    async def get_last_word(self, telegram_id: int) -> str | None:
        """возвращает последнее выданное пользователю слово"""
        statement = select(user_last_word_table.c.word).where(
            user_last_word_table.c.telegram_id == telegram_id
        )
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                value = result.scalar_one_or_none()
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось получить последнее слово для telegram_id={telegram_id}."
            ) from error

        return None if value is None else str(value)

    async def add_favorite(self, telegram_id: int, word: str) -> bool:
        """добавляет слово в избранное, возвращает False при дубле"""
        statement = (
            sqlite_insert(favorites_table)
            .values(telegram_id=telegram_id, word=word)
            .on_conflict_do_nothing(
                index_elements=[favorites_table.c.telegram_id, favorites_table.c.word]
            )
        )
        try:
            async with self._engine.begin() as connection:
                # rowcount отличает вставку от конфликта, а except
                # IntegrityError гасил бы заодно и чужие нарушения целостности
                result = await connection.execute(statement)
                return result.rowcount > 0
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось добавить в избранное для telegram_id={telegram_id}."
            ) from error

    async def get_favorites(self, telegram_id: int) -> list[str]:
        """возвращает избранные слова пользователя"""
        statement = (
            select(favorites_table.c.word)
            .where(favorites_table.c.telegram_id == telegram_id)
            .order_by(favorites_table.c.id)
        )
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                rows = result.fetchall()
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось получить избранное для telegram_id={telegram_id}."
            ) from error

        return [str(row[0]) for row in rows]

    async def clear_favorites(self, telegram_id: int) -> None:
        """очищает избранное пользователя"""
        statement = delete(favorites_table).where(
            favorites_table.c.telegram_id == telegram_id
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось очистить избранное для telegram_id={telegram_id}."
            ) from error

    async def get_game_word_statistics(self) -> dict[int, dict[str, list[str]]]:
        """возвращает историю слов словесных игр как {telegram_id: {game_id: слова}}"""
        statement = select(
            user_game_words_table.c.telegram_id,
            user_game_words_table.c.game_id,
            user_game_words_table.c.word,
        )
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                rows = result.fetchall()
        except SQLAlchemyError as error:
            raise DatabaseError("Не удалось получить историю слов игр.") from error

        grouped: dict[int, dict[str, list[str]]] = {}
        for telegram_id, game_id, word in rows:
            by_game = grouped.setdefault(int(telegram_id), {})
            by_game.setdefault(str(game_id), []).append(str(word))
        return {
            telegram_id: {
                game_id: sorted(words) for game_id, words in sorted(by_game.items())
            }
            for telegram_id, by_game in sorted(grouped.items())
        }

    async def get_summary_totals(self) -> SummaryTotals:
        """считает сводку агрегатами в SQL, не выгружая историю в память"""
        issuances = _issuances_subquery()
        users_statement = select(func.count(func.distinct(issuances.c.telegram_id)))
        words_statement = select(func.count()).select_from(user_game_words_table)
        try:
            async with self._engine.connect() as connection:
                users = (await connection.execute(users_statement)).scalar_one()
                game_words = (await connection.execute(words_statement)).scalar_one()
        except SQLAlchemyError as error:
            raise DatabaseError("Не удалось посчитать сводку.") from error

        return SummaryTotals(users=int(users), game_words=int(game_words))

    async def get_top_game_words(self, limit: int) -> list[tuple[str, int]]:
        """возвращает самые частые слова словесных игр с числом выдач"""
        statement = (
            select(user_game_words_table.c.word, func.count().label("uses"))
            .group_by(user_game_words_table.c.word)
            .order_by(func.count().desc(), user_game_words_table.c.word)
            .limit(limit)
        )
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                rows = result.fetchall()
        except SQLAlchemyError as error:
            raise DatabaseError("Не удалось посчитать топ слов.") from error

        return [(str(row[0]), int(row[1])) for row in rows]

    async def delete_user_data(self, telegram_id: int) -> int:
        """удаляет все личные данные пользователя, возвращает число строк

        снапшот активной партии из session_state удаляется целиком, если
        пользователь в нём упоминается: схемы снапшотов у игр разные и держат
        связанные структуры (пары обсуждений, очередь голосования, руки,
        параллельные списки объясняющих), поэтому вырезать одного игрока из
        json нельзя, не оставив партию в противоречивом состоянии
        """
        removed = 0
        try:
            async with self._engine.begin() as connection:
                for table_name in _USER_OWNED_TABLE_NAMES:
                    table = metadata.tables[table_name]
                    result = await connection.execute(
                        delete(table).where(table.c.telegram_id == telegram_id)
                    )
                    removed += result.rowcount
                removed += await self._delete_user_sessions(connection, telegram_id)
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось удалить данные telegram_id={telegram_id}."
            ) from error

        return removed

    async def _delete_user_sessions(
        self, connection: AsyncConnection, telegram_id: int
    ) -> int:
        """удаляет снапшоты сессий, в которых упоминается пользователь"""
        result = await connection.execute(
            select(
                session_state_table.c.scope,
                session_state_table.c.key,
                session_state_table.c.data,
            )
        )
        removed = 0
        for scope, key, data in result.fetchall():
            # нечитаемый снапшот всё равно не восстановится при старте, а
            # исключение здесь выключило бы удаление данных для всех сразу
            snapshot = _parse_snapshot_or_none(str(scope), str(key), str(data))
            if snapshot is not None and not _snapshot_mentions_user(
                snapshot, telegram_id
            ):
                continue
            await connection.execute(
                delete(session_state_table).where(
                    session_state_table.c.scope == scope,
                    session_state_table.c.key == key,
                )
            )
            removed += 1

        return removed

    async def count_issuances_since(self, cutoff_iso: str) -> int:
        """считает выдачи во всех играх с момента cutoff_iso"""
        issuances = _issuances_subquery()
        statement = (
            select(func.count())
            .select_from(issuances)
            .where(issuances.c.issued_at >= cutoff_iso)
        )
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                count = result.scalar_one()
        except SQLAlchemyError as error:
            raise DatabaseError("Не удалось посчитать выдачи за период.") from error

        return int(count)

    async def count_active_users_since(self, cutoff_iso: str) -> int:
        """считает уникальных пользователей с выдачами с момента cutoff_iso"""
        issuances = _issuances_subquery()
        statement = select(func.count(func.distinct(issuances.c.telegram_id))).where(
            issuances.c.issued_at >= cutoff_iso
        )
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                count = result.scalar_one()
        except SQLAlchemyError as error:
            raise DatabaseError(
                "Не удалось посчитать активных пользователей."
            ) from error

        return int(count)

    async def issuances_by_day(self, cutoff_iso: str) -> list[tuple[str, int]]:
        """возвращает количество выдач по дням с момента cutoff_iso"""
        issuances = _issuances_subquery()
        day = func.substr(issuances.c.issued_at, 1, 10).label("day")
        statement = (
            select(day, func.count())
            .where(issuances.c.issued_at >= cutoff_iso)
            .group_by(day)
            .order_by(day)
        )
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                rows = result.fetchall()
        except SQLAlchemyError as error:
            raise DatabaseError("Не удалось получить активность по дням.") from error

        return [(str(row[0]), int(row[1])) for row in rows]

    async def save_session(self, scope: str, key: str, data: str) -> None:
        """сохраняет снапшот одной сессии scope (upsert по ключу)"""
        now = _now_iso()
        statement = sqlite_insert(session_state_table).values(
            scope=scope, key=key, data=data, updated_at=now
        )
        statement = statement.on_conflict_do_update(
            index_elements=[
                session_state_table.c.scope,
                session_state_table.c.key,
            ],
            set_={"data": data, "updated_at": now},
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось сохранить сессию scope={scope}, key={key}."
            ) from error

    async def delete_session(self, scope: str, key: str) -> None:
        """удаляет снапшот одной сессии scope по ключу"""
        statement = delete(session_state_table).where(
            session_state_table.c.scope == scope,
            session_state_table.c.key == key,
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось удалить сессию scope={scope}, key={key}."
            ) from error

    async def delete_stale_sessions(self, cutoff_iso: str) -> int:
        """удаляет снапшоты сессий, не обновлявшиеся с cutoff_iso

        снапшоты без updated_at остались от старой схемы: считаем их
        протухшими, потому что дата последней активности неизвестна
        """
        statement = delete(session_state_table).where(
            (session_state_table.c.updated_at.is_(None))
            | (session_state_table.c.updated_at < cutoff_iso)
        )
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement)
        except SQLAlchemyError as error:
            raise DatabaseError("Не удалось убрать протухшие сессии.") from error

        return result.rowcount

    async def load_session_scope(self, scope: str) -> dict[str, str]:
        """возвращает снапшоты активных сессий scope как {key: json}"""
        statement = select(session_state_table.c.key, session_state_table.c.data).where(
            session_state_table.c.scope == scope
        )
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                rows = result.fetchall()
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось загрузить состояние сессий scope={scope}."
            ) from error

        return {str(row[0]): str(row[1]) for row in rows}
