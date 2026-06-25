from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import (
    Column,
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
    union,
    union_all,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    create_async_engine,
)

from constants import (
    BOSSES_HISTORY_KEY,
    CURSES_HISTORY_KEY,
    USER_BOSSES_TABLE_NAME,
    USER_CURSES_TABLE_NAME,
    USER_WORDS_TABLE_NAME,
    WORDS_HISTORY_KEY,
)
from exceptions import DuplicateHistoryItemError
from services.random_generator import Boss, Curse

metadata = MetaData()

user_words_table = Table(
    USER_WORDS_TABLE_NAME,
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("telegram_id", Integer, nullable=False),
    Column("word", String, nullable=False),
    Column("issued_at", String, nullable=True),
    UniqueConstraint("telegram_id", "word"),
)

user_curses_table = Table(
    USER_CURSES_TABLE_NAME,
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("telegram_id", Integer, nullable=False),
    Column("curse_id", String, nullable=False),
    Column("issued_at", String, nullable=True),
    UniqueConstraint("telegram_id", "curse_id"),
)

user_bosses_table = Table(
    USER_BOSSES_TABLE_NAME,
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("telegram_id", Integer, nullable=False),
    Column("boss_id", String, nullable=False),
    Column("issued_at", String, nullable=True),
    UniqueConstraint("telegram_id", "boss_id"),
)

user_game_words_table = Table(
    "user_game_words",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("telegram_id", Integer, nullable=False),
    Column("game_id", String, nullable=False),
    Column("word", String, nullable=False),
    Column("issued_at", String, nullable=True),
    UniqueConstraint("telegram_id", "game_id", "word"),
)

custom_words_table = Table(
    "custom_words",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("game_id", String, nullable=False),
    Column("word", String, nullable=False),
    UniqueConstraint("game_id", "word"),
)

custom_curses_table = Table(
    "custom_curses",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("title", String, nullable=False),
    Column("description", String, nullable=False),
)

custom_bosses_table = Table(
    "custom_bosses",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String, nullable=False),
    Column("description", String, nullable=False),
)


_HISTORY_TABLE_NAMES: tuple[str, ...] = (
    USER_WORDS_TABLE_NAME,
    USER_CURSES_TABLE_NAME,
    USER_BOSSES_TABLE_NAME,
    "user_game_words",
)


def _now_iso() -> str:
    """возвращает текущее время в ISO-формате UTC"""
    return datetime.now(timezone.utc).isoformat()


def iso_days_ago(days: int) -> str:
    """возвращает ISO-метку времени days дней назад (UTC)"""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _issuances_subquery() -> Subquery:
    """строит объединение всех выдач как (telegram_id, issued_at)"""
    return union_all(
        select(
            user_words_table.c.telegram_id.label("telegram_id"),
            user_words_table.c.issued_at.label("issued_at"),
        ),
        select(
            user_curses_table.c.telegram_id.label("telegram_id"),
            user_curses_table.c.issued_at.label("issued_at"),
        ),
        select(
            user_bosses_table.c.telegram_id.label("telegram_id"),
            user_bosses_table.c.issued_at.label("issued_at"),
        ),
        select(
            user_game_words_table.c.telegram_id.label("telegram_id"),
            user_game_words_table.c.issued_at.label("issued_at"),
        ),
    ).subquery()


class DatabaseError(RuntimeError):
    """ошибка работы с базой данных"""

    pass


class SQLiteHistoryStorage:
    def __init__(self, database_path: Path) -> None:
        """создаёт хранилище истории выдач"""
        self._database_path: Path = database_path
        self._engine: AsyncEngine = create_async_engine(
            f"sqlite+aiosqlite:///{database_path.as_posix()}"
        )

    async def initialize(self) -> None:
        """создаёт каталог, таблицы и недостающие колонки при запуске бота"""
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with self._engine.begin() as connection:
                await connection.run_sync(metadata.create_all)
                for table_name in _HISTORY_TABLE_NAMES:
                    await self._ensure_issued_at_column(connection, table_name)
        except SQLAlchemyError as error:
            raise DatabaseError(
                "Не удалось инициализировать SQLite-базу."
            ) from error

    async def _ensure_issued_at_column(
        self, connection: AsyncConnection, table_name: str
    ) -> None:
        """добавляет колонку issued_at в существующую таблицу, если её нет"""
        result = await connection.execute(
            text(f"PRAGMA table_info({table_name})")
        )
        columns = {row[1] for row in result.fetchall()}
        if "issued_at" not in columns:
            await connection.execute(
                text(f"ALTER TABLE {table_name} ADD COLUMN issued_at TEXT")
            )

    async def get_user_words(self, telegram_id: int) -> set[str]:
        """возвращает слова, уже выданные пользователю"""
        return await self._get_user_items(
            user_words_table, "word", telegram_id
        )

    async def save_user_word(self, telegram_id: int, word: str) -> None:
        """сохраняет выданное пользователю слово"""
        await self._save_user_item(user_words_table, "word", telegram_id, word)

    async def reset_user_words(self, telegram_id: int) -> None:
        """очищает историю слов пользователя"""
        await self._reset_user_items(user_words_table, telegram_id)

    async def get_user_curses(self, telegram_id: int) -> set[str]:
        """возвращает проклятья, уже выданные пользователю"""
        return await self._get_user_items(
            user_curses_table, "curse_id", telegram_id
        )

    async def save_user_curse(self, telegram_id: int, curse_id: str) -> None:
        """сохраняет выданное пользователю проклятье"""
        await self._save_user_item(
            user_curses_table, "curse_id", telegram_id, curse_id
        )

    async def reset_user_curses(self, telegram_id: int) -> None:
        """очищает историю проклятий пользователя для нового круга"""
        await self._reset_user_items(user_curses_table, telegram_id)

    async def get_user_bosses(self, telegram_id: int) -> set[str]:
        """возвращает боссов, уже выданных пользователю"""
        return await self._get_user_items(
            user_bosses_table, "boss_id", telegram_id
        )

    async def save_user_boss(self, telegram_id: int, boss_id: str) -> None:
        """сохраняет выданного пользователю босса"""
        await self._save_user_item(
            user_bosses_table, "boss_id", telegram_id, boss_id
        )

    async def reset_user_bosses(self, telegram_id: int) -> None:
        """очищает историю боссов пользователя для нового круга"""
        await self._reset_user_items(user_bosses_table, telegram_id)

    async def reset_user_all(self, telegram_id: int) -> None:
        """очищает всю историю выдач пользователя"""
        await self.reset_user_words(telegram_id)
        await self.reset_user_curses(telegram_id)
        await self.reset_user_bosses(telegram_id)

    async def get_user_game_words(
        self, telegram_id: int, game_id: str
    ) -> set[str]:
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

    async def reset_user_game_words(
        self, telegram_id: int, game_id: str
    ) -> None:
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

    async def count_user_game_words(
        self, telegram_id: int, game_id: str
    ) -> int:
        """возвращает количество выданных слов словесной игры"""
        statement = (
            select(func.count())
            .select_from(user_game_words_table)
            .where(
                user_game_words_table.c.telegram_id == telegram_id,
                user_game_words_table.c.game_id == game_id,
            )
        )
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                count = result.scalar_one()
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось посчитать слова игры {game_id} для telegram_id={telegram_id}."
            ) from error

        return int(count)

    async def add_custom_word(self, game_id: str, word: str) -> None:
        """добавляет пользовательское слово в пул игры"""
        statement = insert(custom_words_table).values(
            game_id=game_id, word=word
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
        """добавляет пользовательское проклятье"""
        statement = insert(custom_curses_table).values(
            title=title, description=description
        )
        try:
            async with self._engine.begin() as connection:
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
        """добавляет пользовательского босса"""
        statement = insert(custom_bosses_table).values(
            name=name, description=description
        )
        try:
            async with self._engine.begin() as connection:
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

    async def get_user_statistics(
        self, telegram_id: int
    ) -> dict[str, list[str]]:
        """возвращает полную историю выдач пользователя"""
        return {
            WORDS_HISTORY_KEY: sorted(await self.get_user_words(telegram_id)),
            CURSES_HISTORY_KEY: sorted(
                await self.get_user_curses(telegram_id)
            ),
            BOSSES_HISTORY_KEY: sorted(
                await self.get_user_bosses(telegram_id)
            ),
        }

    async def get_all_user_statistics(self) -> dict[int, dict[str, list[str]]]:
        """возвращает полную историю выдач всех пользователей"""
        telegram_ids = await self._get_all_telegram_ids()
        statistics: dict[int, dict[str, list[str]]] = {}
        for telegram_id in telegram_ids:
            statistics[telegram_id] = await self.get_user_statistics(
                telegram_id
            )
        return statistics

    async def count_user_words(self, telegram_id: int) -> int:
        """возвращает количество выданных пользователю слов"""
        return await self._count_user_items(user_words_table, telegram_id)

    async def count_user_curses(self, telegram_id: int) -> int:
        """возвращает количество выданных пользователю проклятий"""
        return await self._count_user_items(user_curses_table, telegram_id)

    async def count_user_bosses(self, telegram_id: int) -> int:
        """возвращает количество выданных пользователю боссов"""
        return await self._count_user_items(user_bosses_table, telegram_id)

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
            raise DatabaseError(
                "Не удалось посчитать выдачи за период."
            ) from error

        return int(count)

    async def count_active_users_since(self, cutoff_iso: str) -> int:
        """считает уникальных пользователей с выдачами с момента cutoff_iso"""
        issuances = _issuances_subquery()
        statement = select(
            func.count(func.distinct(issuances.c.telegram_id))
        ).where(issuances.c.issued_at >= cutoff_iso)
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                count = result.scalar_one()
        except SQLAlchemyError as error:
            raise DatabaseError(
                "Не удалось посчитать активных пользователей."
            ) from error

        return int(count)

    async def issuances_by_day(
        self, cutoff_iso: str
    ) -> list[tuple[str, int]]:
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
            raise DatabaseError(
                "Не удалось получить активность по дням."
            ) from error

        return [(str(row[0]), int(row[1])) for row in rows]

    async def _get_user_items(
        self, table: Table, item_column: str, telegram_id: int
    ) -> set[str]:
        """возвращает значения из таблицы истории"""
        column = table.c[item_column]
        statement = select(column).where(table.c.telegram_id == telegram_id)
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                rows = result.fetchall()
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось получить историю из таблицы {table.name} для telegram_id={telegram_id}."
            ) from error

        return {str(row[0]) for row in rows}

    async def _get_all_telegram_ids(self) -> list[int]:
        """возвращает все telegram id с историей выдач"""
        users_query = union(
            select(user_words_table.c.telegram_id),
            select(user_curses_table.c.telegram_id),
            select(user_bosses_table.c.telegram_id),
        ).subquery()
        statement = select(users_query.c.telegram_id).order_by(
            users_query.c.telegram_id
        )
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                rows = result.fetchall()
        except SQLAlchemyError as error:
            raise DatabaseError(
                "Не удалось получить список пользователей со статистикой."
            ) from error

        return [int(row[0]) for row in rows]

    async def _count_user_items(self, table: Table, telegram_id: int) -> int:
        """возвращает количество значений в таблице истории"""
        statement = (
            select(func.count())
            .select_from(table)
            .where(table.c.telegram_id == telegram_id)
        )
        try:
            async with self._engine.connect() as connection:
                result = await connection.execute(statement)
                count = result.scalar_one()
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось посчитать элементы в таблице {table.name} для telegram_id={telegram_id}."
            ) from error

        return int(count)

    async def _save_user_item(
        self,
        table: Table,
        item_column: str,
        telegram_id: int,
        item_id: str,
    ) -> None:
        """сохраняет значение в таблицу истории"""
        statement = insert(table).values(
            telegram_id=telegram_id,
            issued_at=_now_iso(),
            **{item_column: item_id},
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except IntegrityError as error:
            raise DuplicateHistoryItemError(
                f"Элемент уже сохранён в таблице {table.name}: telegram_id={telegram_id}, item_id={item_id}."
            ) from error
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось сохранить элемент в таблицу {table.name}: telegram_id={telegram_id}, item_id={item_id}."
            ) from error

    async def _reset_user_items(self, table: Table, telegram_id: int) -> None:
        """удаляет значения из таблицы истории"""
        statement = delete(table).where(table.c.telegram_id == telegram_id)
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise DatabaseError(
                f"Не удалось очистить историю в таблице {table.name} для telegram_id={telegram_id}."
            ) from error
