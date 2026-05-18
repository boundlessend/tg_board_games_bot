import logging

from aiogram import F, Router
from aiogram.types import Message

from constants import (
    ADMIN_CLOSE_TITLE,
    ADMIN_SECRET_PHRASE,
    ADMIN_STATS_TITLE,
    BOSSES_HISTORY_KEY,
    BOSSES_LIMIT,
    CURSES_HISTORY_KEY,
    CURSES_LIMIT,
    TELEGRAM_MESSAGE_LIMIT,
    WORDS_HISTORY_KEY,
    WORDS_LIMIT,
)
from database import DatabaseError, SQLiteHistoryStorage
from keyboards import create_admin_keyboard, create_main_menu_keyboard
from services.random_generator import Boss, Curse, DangerousWordsContent

logger = logging.getLogger(__name__)


def create_admin_router(
    content: DangerousWordsContent, storage: SQLiteHistoryStorage
) -> Router:
    """создаёт роутер секретного админ-меню"""
    router = Router()
    admin_ids: set[int] = set()

    @router.message(F.text == ADMIN_SECRET_PHRASE)
    async def handle_admin_secret(message: Message) -> None:
        if not _is_private_chat(message):
            return

        telegram_id = _extract_telegram_id(message)
        admin_ids.add(telegram_id)
        await message.answer(
            "В админку войдено", reply_markup=create_admin_keyboard()
        )
        await _send_all_statistics(message, content, storage)

    @router.message(F.text == ADMIN_CLOSE_TITLE)
    async def handle_admin_close(message: Message) -> None:
        if not _is_private_chat(message):
            return

        telegram_id = _extract_telegram_id(message)
        admin_ids.discard(telegram_id)
        await message.answer(
            "Из админки выйдено", reply_markup=create_main_menu_keyboard()
        )

    @router.message(F.text == ADMIN_STATS_TITLE)
    async def handle_admin_stats_request(message: Message) -> None:
        if not _is_private_chat(message):
            return

        telegram_id = _extract_telegram_id(message)
        if telegram_id not in admin_ids:
            await message.answer("Выберите действие с помощью кнопок меню.")
            return

        await _send_all_statistics(message, content, storage)

    return router


def _extract_telegram_id(message: Message) -> int:
    """возвращает telegram id отправителя сообщения"""
    if message.from_user is None:
        raise DatabaseError("Не удалось определить telegram_id пользователя.")
    return message.from_user.id


def _is_private_chat(message: Message) -> bool:
    """проверяет что сообщение пришло из личного чата"""
    return message.chat.type == "private"


async def _send_all_statistics(
    message: Message,
    content: DangerousWordsContent,
    storage: SQLiteHistoryStorage,
) -> None:
    """отправляет администратору статистику по всем пользователям"""
    telegram_id = _extract_telegram_id(message)
    try:
        statistics = await storage.get_all_user_statistics()
    except DatabaseError:
        logger.exception(
            "database_error",
            extra={"telegram_id": telegram_id, "action": "admin_all_stats"},
        )
        await message.answer("Не удалось получить статистику.")
        return

    report = _build_all_statistics_report(content, statistics)
    for chunk in _split_report(report):
        await message.answer(chunk, reply_markup=create_admin_keyboard())


def _build_all_statistics_report(
    content: DangerousWordsContent,
    statistics: dict[int, dict[str, list[str]]],
) -> str:
    """собирает общий отчёт по всем пользователям"""
    if len(statistics) == 0:
        return "Статистика пока пустая."

    reports = [
        _build_user_statistics_report(content, telegram_id, user_statistics)
        for telegram_id, user_statistics in statistics.items()
    ]
    return "\n\n---\n\n".join(reports)


def _build_user_statistics_report(
    content: DangerousWordsContent,
    telegram_id: int,
    statistics: dict[str, list[str]],
) -> str:
    """собирает текстовый отчёт по истории пользователя"""
    curse_titles = _create_curse_titles_map(content.curses)
    boss_names = _create_boss_names_map(content.bosses)

    words = statistics[WORDS_HISTORY_KEY]
    curse_ids = statistics[CURSES_HISTORY_KEY]
    boss_ids = statistics[BOSSES_HISTORY_KEY]

    sections = [
        f"Статистика пользователя {telegram_id}",
        f"Слова: {len(words)}/{WORDS_LIMIT}",
        _format_values(words),
        f"Проклятья: {len(curse_ids)}/{CURSES_LIMIT}",
        _format_values(
            [_format_curse(curse_id, curse_titles) for curse_id in curse_ids]
        ),
        f"Боссы: {len(boss_ids)}/{BOSSES_LIMIT}",
        _format_values(
            [_format_boss(boss_id, boss_names) for boss_id in boss_ids]
        ),
    ]
    return "\n\n".join(sections)


def _create_curse_titles_map(curses: list[Curse]) -> dict[str, str]:
    """создаёт словарь названий проклятий по id"""
    return {curse.id: curse.title for curse in curses}


def _create_boss_names_map(bosses: list[Boss]) -> dict[str, str]:
    """создаёт словарь имён боссов по id"""
    return {boss.id: boss.name for boss in bosses}


def _format_curse(curse_id: str, curse_titles: dict[str, str]) -> str:
    """форматирует проклятье для админского отчёта"""
    title = curse_titles.get(curse_id, "неизвестное проклятье")
    return f"{curse_id} — {title}"


def _format_boss(boss_id: str, boss_names: dict[str, str]) -> str:
    """форматирует босса для админского отчёта"""
    name = boss_names.get(boss_id, "неизвестный босс")
    return f"{boss_id} — {name}"


def _format_values(values: list[str]) -> str:
    """форматирует список значений для отчёта"""
    if len(values) == 0:
        return "пока пусто"
    return "\n".join(values)


def _split_report(report: str) -> list[str]:
    """делит длинный отчёт на сообщения telegram"""
    chunks: list[str] = []
    current_lines: list[str] = []
    current_length = 0

    for line in report.splitlines():
        line_length = len(line) + 1
        if (
            current_length + line_length > TELEGRAM_MESSAGE_LIMIT
            and len(current_lines) > 0
        ):
            chunks.append("\n".join(current_lines))
            current_lines = []
            current_length = 0
        current_lines.append(line)
        current_length += line_length

    if len(current_lines) > 0:
        chunks.append("\n".join(current_lines))

    return chunks
