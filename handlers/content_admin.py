import json
import logging
import re
import tempfile
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import FSInputFile, Message

from constants import DANGEROUS_WORDS_GAME_ID
from database import DatabaseError, SQLiteHistoryStorage
from exceptions import DuplicateHistoryItemError
from services.random_generator import WordGame

logger = logging.getLogger(__name__)


def create_content_admin_router(
    storage: SQLiteHistoryStorage,
    admin_ids: frozenset[int],
    word_games: list[WordGame],
) -> Router:
    """создаёт роутер админских команд добавления контента"""
    router = Router()
    word_pools = {DANGEROUS_WORDS_GAME_ID} | {
        game.game_id for game in word_games
    }

    @router.message(Command("addword"))
    async def handle_add_word(
        message: Message, command: CommandObject
    ) -> None:
        if not _is_admin(message, admin_ids):
            return

        parts = (command.args or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(_addword_usage(word_pools))
            return

        game_id = parts[0].strip()
        word = parts[1].strip().lower()
        if game_id not in word_pools:
            await message.answer(_addword_usage(word_pools))
            return
        if word == "":
            await message.answer("Слово пустое.")
            return

        try:
            await storage.add_custom_word(game_id, word)
        except DuplicateHistoryItemError:
            await message.answer(f"Слово уже есть в пуле {game_id}.")
            return
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={
                    "telegram_id": (
                        message.from_user.id if message.from_user else None
                    ),
                    "action": "add_word",
                },
            )
            await message.answer("Не удалось добавить слово.")
            return

        await message.answer(f"Добавлено в «{game_id}»: {word}")

    @router.message(Command("addcurse"))
    async def handle_add_curse(
        message: Message, command: CommandObject
    ) -> None:
        if not _is_admin(message, admin_ids):
            return

        pair = _parse_pair(command.args)
        if pair is None:
            await message.answer("Формат: /addcurse <название> | <описание>")
            return

        title, description = pair
        try:
            await storage.add_custom_curse(title, description)
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={
                    "telegram_id": (
                        message.from_user.id if message.from_user else None
                    ),
                    "action": "add_curse",
                },
            )
            await message.answer("Не удалось добавить проклятье.")
            return

        await message.answer(f"Проклятье добавлено: {title}")

    @router.message(Command("addboss"))
    async def handle_add_boss(
        message: Message, command: CommandObject
    ) -> None:
        if not _is_admin(message, admin_ids):
            return

        pair = _parse_pair(command.args)
        if pair is None:
            await message.answer("Формат: /addboss <имя> | <описание>")
            return

        name, description = pair
        try:
            await storage.add_custom_boss(name, description)
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={
                    "telegram_id": (
                        message.from_user.id if message.from_user else None
                    ),
                    "action": "add_boss",
                },
            )
            await message.answer("Не удалось добавить босса.")
            return

        await message.answer(f"Босс добавлен: {name}")

    @router.message(Command("backup"))
    async def handle_backup(message: Message) -> None:
        if not _is_admin(message, admin_ids):
            return

        document = FSInputFile(
            storage.database_path, filename="bot.sqlite3"
        )
        await message.answer_document(document, caption="Бэкап базы")

    @router.message(_is_restore_document)
    async def handle_restore(message: Message) -> None:
        if not _is_admin(message, admin_ids):
            return

        document = message.document
        bot = message.bot
        if document is None or bot is None:
            return

        destination = Path(tempfile.mkdtemp()) / "restore.sqlite3"
        try:
            await bot.download(document, destination=destination)
        except Exception:
            logger.exception("download_failed", extra={"action": "restore"})
            await message.answer("Не удалось скачать файл.")
            return

        if not _is_sqlite_file(destination):
            await message.answer("Это не похоже на файл SQLite-базы.")
            return

        try:
            await storage.replace_database(destination)
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={
                    "telegram_id": (
                        message.from_user.id if message.from_user else None
                    ),
                    "action": "restore",
                },
            )
            await message.answer("Не удалось восстановить базу.")
            return

        await message.answer("База восстановлена из файла.")

    @router.message(Command("restore"))
    async def handle_restore_hint(message: Message) -> None:
        if not _is_admin(message, admin_ids):
            return
        await message.answer(
            "Пришлите файл базы (.sqlite3) с подписью /restore."
        )

    @router.message(Command("listcontent"))
    async def handle_list_content(message: Message) -> None:
        if not _is_admin(message, admin_ids):
            return

        try:
            sections = ["Пользовательский контент:"]
            for game_id in sorted(word_pools):
                words = await storage.get_custom_words(game_id)
                sections.append(f"{game_id}: {_format_items(words)}")
            curses = await storage.get_custom_curses()
            sections.append(
                "проклятья: "
                + _format_items([f"{c.id} - {c.title}" for c in curses])
            )
            bosses = await storage.get_custom_bosses()
            sections.append(
                "боссы: "
                + _format_items([f"{b.id} - {b.name}" for b in bosses])
            )
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"action": "list_content"},
            )
            await message.answer("Не удалось получить контент.")
            return

        await message.answer("\n".join(sections))

    @router.message(Command("delword"))
    async def handle_del_word(
        message: Message, command: CommandObject
    ) -> None:
        if not _is_admin(message, admin_ids):
            return

        parts = (command.args or "").strip().split(maxsplit=1)
        if len(parts) < 2 or parts[0] not in word_pools:
            await message.answer(
                f"Формат: /delword <игра> <слово>\n"
                f"Игры: {', '.join(sorted(word_pools))}"
            )
            return

        try:
            deleted = await storage.delete_custom_word(
                parts[0], parts[1].strip().lower()
            )
        except DatabaseError:
            logger.exception(
                "database_error", extra={"action": "del_word"}
            )
            await message.answer("Не удалось удалить слово.")
            return

        await message.answer(
            "Удалено." if deleted else "Такого пользовательского слова нет."
        )

    @router.message(Command("delcurse"))
    async def handle_del_curse(
        message: Message, command: CommandObject
    ) -> None:
        if not _is_admin(message, admin_ids):
            return

        row_id = _parse_custom_id(command.args, "cc_")
        if row_id is None:
            await message.answer("Формат: /delcurse <id> (например cc_3)")
            return

        try:
            deleted = await storage.delete_custom_curse(row_id)
        except DatabaseError:
            logger.exception(
                "database_error", extra={"action": "del_curse"}
            )
            await message.answer("Не удалось удалить проклятье.")
            return

        await message.answer("Удалено." if deleted else "Такого проклятья нет.")

    @router.message(Command("delboss"))
    async def handle_del_boss(
        message: Message, command: CommandObject
    ) -> None:
        if not _is_admin(message, admin_ids):
            return

        row_id = _parse_custom_id(command.args, "cb_")
        if row_id is None:
            await message.answer("Формат: /delboss <id> (например cb_3)")
            return

        try:
            deleted = await storage.delete_custom_boss(row_id)
        except DatabaseError:
            logger.exception(
                "database_error", extra={"action": "del_boss"}
            )
            await message.answer("Не удалось удалить босса.")
            return

        await message.answer("Удалено." if deleted else "Такого босса нет.")

    @router.message(_is_import_words_document)
    async def handle_import_words(message: Message) -> None:
        if not _is_admin(message, admin_ids):
            return

        caption_parts = (message.caption or "").strip().split()
        if len(caption_parts) < 2 or caption_parts[1] not in word_pools:
            await message.answer(
                f"Подпись: /importwords <игра>\n"
                f"Игры: {', '.join(sorted(word_pools))}"
            )
            return

        game_id = caption_parts[1]
        document = message.document
        bot = message.bot
        if document is None or bot is None:
            return

        destination = Path(tempfile.mkdtemp()) / "pack"
        try:
            await bot.download(document, destination=destination)
        except Exception:
            logger.exception("download_failed", extra={"action": "import"})
            await message.answer("Не удалось скачать файл.")
            return

        try:
            words = _parse_words_pack(destination.read_bytes())
        except (OSError, ValueError):
            await message.answer(
                "Не удалось разобрать файл (ожидается JSON-массив "
                "или слова по строкам/через запятую)."
            )
            return

        try:
            added, skipped = await _import_words(storage, game_id, words)
        except DatabaseError:
            logger.exception("database_error", extra={"action": "import"})
            await message.answer("Ошибка при импорте.")
            return

        await message.answer(
            f"Импорт в «{game_id}»: добавлено {added}, пропущено {skipped}."
        )

    return router


def _is_admin(message: Message, admin_ids: frozenset[int]) -> bool:
    """проверяет что команда от администратора из личного чата"""
    if message.chat.type != "private":
        return False
    if message.from_user is None:
        return False
    return message.from_user.id in admin_ids


def _addword_usage(word_pools: set[str]) -> str:
    """текст подсказки по команде добавления слова"""
    pools = ", ".join(sorted(word_pools))
    return f"Формат: /addword <игра> <слово>\nИгры: {pools}"


def _parse_pair(args: str | None) -> tuple[str, str] | None:
    """разбирает строку вида 'левая часть | правая часть'"""
    if args is None or "|" not in args:
        return None

    left, right = args.split("|", 1)
    left = left.strip()
    right = right.strip()
    if left == "" or right == "":
        return None

    return left, right


def _is_restore_document(message: Message) -> bool:
    """сообщение с файлом и подписью /restore"""
    if message.document is None or message.caption is None:
        return False
    return message.caption.strip().startswith("/restore")


def _is_sqlite_file(path: Path) -> bool:
    """проверяет сигнатуру файла SQLite по заголовку"""
    try:
        with path.open("rb") as file:
            header = file.read(16)
    except OSError:
        return False
    return header == b"SQLite format 3\x00"


def _is_import_words_document(message: Message) -> bool:
    """сообщение с файлом и подписью /importwords"""
    if message.document is None or message.caption is None:
        return False
    return message.caption.strip().startswith("/importwords")


def _format_items(items: list[str]) -> str:
    """форматирует список элементов контента для отчёта"""
    if len(items) == 0:
        return "пусто"
    return ", ".join(items)


def _parse_custom_id(value: str | None, prefix: str) -> int | None:
    """извлекает числовой id из значения вида cc_3 / cb_3 / 3"""
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned.startswith(prefix):
        cleaned = cleaned[len(prefix) :]
    try:
        return int(cleaned)
    except ValueError:
        return None


def _parse_words_pack(raw: bytes) -> list[str]:
    """разбирает пак слов из JSON-массива или текста по разделителям"""
    text = raw.decode("utf-8", errors="replace").strip()
    if text == "":
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError("некорректный JSON-пак") from error
        if not isinstance(data, list):
            raise ValueError("JSON-пак должен быть списком")
        return [str(item).strip() for item in data if str(item).strip()]
    parts = re.split(r"[\n,;\t]+", text)
    return [part.strip() for part in parts if part.strip()]


async def _import_words(
    storage: SQLiteHistoryStorage, game_id: str, words: list[str]
) -> tuple[int, int]:
    """массово добавляет слова в пул игры, возвращает (добавлено, пропущено)"""
    added = 0
    skipped = 0
    for word in words:
        normalized = word.strip().lower()
        if normalized == "":
            continue
        try:
            await storage.add_custom_word(game_id, normalized)
            added += 1
        except DuplicateHistoryItemError:
            skipped += 1
    return added, skipped
