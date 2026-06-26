import logging
from collections import Counter

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from constants import (
    BOSSES_HISTORY_KEY,
    BOSSES_LIMIT,
    CB_ADMIN_ACTIVITY,
    CB_ADMIN_CLOSE,
    CB_ADMIN_CSV,
    CB_ADMIN_STATS,
    CURSES_HISTORY_KEY,
    CURSES_LIMIT,
    TELEGRAM_MESSAGE_LIMIT,
    WORDS_HISTORY_KEY,
    WORDS_LIMIT,
)
from database import DatabaseError, SQLiteHistoryStorage, iso_days_ago
from keyboards import create_admin_keyboard, create_private_menu_keyboard
from services.random_generator import (
    Boss,
    Curse,
    DangerousWordsContent,
    WordGame,
)

logger = logging.getLogger(__name__)

ADMIN_CLOSED_TEXT = "Админка закрыта"


def create_admin_router(
    content: DangerousWordsContent,
    storage: SQLiteHistoryStorage,
    admin_ids: frozenset[int],
    word_games: list[WordGame],
) -> Router:
    """создаёт роутер админ-меню с доступом по telegram id"""
    router = Router()

    @router.message(Command("admin"))
    async def handle_admin_open(message: Message) -> None:
        if not _is_authorized_admin_message(message, admin_ids):
            return

        admin_id = _extract_telegram_id(message)
        await _send_summary(message, storage, admin_id)

    @router.callback_query(F.data == CB_ADMIN_STATS)
    async def handle_admin_stats_request(callback: CallbackQuery) -> None:
        if not _is_authorized_admin_callback(callback, admin_ids):
            await callback.answer()
            return

        message = callback.message
        if isinstance(message, Message):
            await _send_all_statistics(
                message, content, storage, callback.from_user.id
            )
        await callback.answer()

    @router.callback_query(F.data == CB_ADMIN_CSV)
    async def handle_admin_csv(callback: CallbackQuery) -> None:
        if not _is_authorized_admin_callback(callback, admin_ids):
            await callback.answer()
            return

        message = callback.message
        if not isinstance(message, Message):
            await callback.answer()
            return

        try:
            statistics = await storage.get_all_user_statistics()
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={
                    "telegram_id": callback.from_user.id,
                    "action": "admin_csv",
                },
            )
            await callback.answer(
                "Не удалось получить статистику.", show_alert=True
            )
            return

        document = BufferedInputFile(
            _build_statistics_csv(statistics).encode("utf-8"),
            filename="stats.csv",
        )
        await message.answer_document(document, caption="Статистика CSV")
        await callback.answer()

    @router.callback_query(F.data == CB_ADMIN_ACTIVITY)
    async def handle_admin_activity(callback: CallbackQuery) -> None:
        if not _is_authorized_admin_callback(callback, admin_ids):
            await callback.answer()
            return

        message = callback.message
        if not isinstance(message, Message):
            await callback.answer()
            return

        try:
            by_day = await storage.issuances_by_day(iso_days_ago(14))
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={
                    "telegram_id": callback.from_user.id,
                    "action": "admin_activity",
                },
            )
            await callback.answer(
                "Не удалось получить активность.", show_alert=True
            )
            return

        await message.answer(
            _format_activity(by_day), reply_markup=create_admin_keyboard()
        )
        await callback.answer()

    @router.callback_query(F.data == CB_ADMIN_CLOSE)
    async def handle_admin_close(callback: CallbackQuery) -> None:
        if not _is_authorized_admin_callback(callback, admin_ids):
            await callback.answer()
            return

        message = callback.message
        if isinstance(message, Message):
            try:
                await message.edit_text(
                    ADMIN_CLOSED_TEXT,
                    reply_markup=create_private_menu_keyboard(word_games),
                )
            except TelegramBadRequest:
                pass
        await callback.answer()

    return router


def _extract_telegram_id(message: Message) -> int:
    """возвращает telegram id отправителя сообщения"""
    if message.from_user is None:
        raise DatabaseError("Не удалось определить telegram_id пользователя.")
    return message.from_user.id


def _is_authorized_admin_message(
    message: Message, admin_ids: frozenset[int]
) -> bool:
    """проверяет что сообщение от администратора из личного чата"""
    if message.chat.type != "private":
        return False
    if message.from_user is None:
        return False
    return message.from_user.id in admin_ids


def _is_authorized_admin_callback(
    callback: CallbackQuery, admin_ids: frozenset[int]
) -> bool:
    """проверяет что callback от администратора из личного чата"""
    message = callback.message
    if message is None or message.chat.type != "private":
        return False
    return callback.from_user.id in admin_ids


async def _send_summary(
    message: Message,
    storage: SQLiteHistoryStorage,
    admin_id: int,
) -> None:
    """отправляет администратору сводку по всем пользователям"""
    try:
        statistics = await storage.get_all_user_statistics()
        recent_issuances = await storage.count_issuances_since(iso_days_ago(7))
        recent_users = await storage.count_active_users_since(iso_days_ago(7))
    except DatabaseError:
        logger.exception(
            "database_error",
            extra={"telegram_id": admin_id, "action": "admin_summary"},
        )
        await message.answer("Не удалось получить статистику.")
        return

    await message.answer(
        _build_summary(statistics, recent_issuances, recent_users),
        reply_markup=create_admin_keyboard(),
    )


def _build_summary(
    statistics: dict[int, dict[str, list[str]]],
    recent_issuances: int,
    recent_users: int,
) -> str:
    """собирает сводку по всем пользователям"""
    if len(statistics) == 0:
        return "Статистика пока пустая."

    total_words = sum(
        len(user[WORDS_HISTORY_KEY]) for user in statistics.values()
    )
    total_curses = sum(
        len(user[CURSES_HISTORY_KEY]) for user in statistics.values()
    )
    total_bosses = sum(
        len(user[BOSSES_HISTORY_KEY]) for user in statistics.values()
    )

    word_counter: Counter[str] = Counter()
    for user in statistics.values():
        word_counter.update(user[WORDS_HISTORY_KEY])
    top_words = [
        f"{word} x{count}" for word, count in word_counter.most_common(10)
    ]

    sections = [
        "Сводка",
        f"Пользователей: {len(statistics)}",
        f"Слов выдано: {total_words}",
        f"Проклятий выдано: {total_curses}",
        f"Боссов выдано: {total_bosses}",
        f"Выдач за 7 дней: {recent_issuances}",
        f"Активных за 7 дней: {recent_users}",
        "Топ-10 слов:",
        _format_values(top_words),
    ]
    return "\n\n".join(sections)


def _format_activity(by_day: list[tuple[str, int]]) -> str:
    """форматирует активность по дням для админского отчёта"""
    if len(by_day) == 0:
        return "Активности за период нет."
    lines = ["Выдачи по дням (14 дней):"]
    lines.extend(f"{day}: {count}" for day, count in by_day)
    return "\n".join(lines)


def _build_statistics_csv(
    statistics: dict[int, dict[str, list[str]]],
) -> str:
    """собирает csv со сводкой выдач по каждому пользователю"""
    lines = ["telegram_id,words,curses,bosses"]
    for telegram_id, user in statistics.items():
        lines.append(
            f"{telegram_id},"
            f"{len(user[WORDS_HISTORY_KEY])},"
            f"{len(user[CURSES_HISTORY_KEY])},"
            f"{len(user[BOSSES_HISTORY_KEY])}"
        )
    return "\n".join(lines) + "\n"


async def _send_all_statistics(
    message: Message,
    content: DangerousWordsContent,
    storage: SQLiteHistoryStorage,
    admin_id: int,
) -> None:
    """отправляет администратору статистику по всем пользователям"""
    try:
        statistics = await storage.get_all_user_statistics()
    except DatabaseError:
        logger.exception(
            "database_error",
            extra={"telegram_id": admin_id, "action": "admin_all_stats"},
        )
        await message.answer("Не удалось получить статистику.")
        return

    report = _build_all_statistics_report(content, statistics)
    chunks = _split_report(report)
    for index, chunk in enumerate(chunks):
        is_last_chunk = index == len(chunks) - 1
        await message.answer(
            chunk,
            reply_markup=create_admin_keyboard() if is_last_chunk else None,
        )


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
    return f"{curse_id} - {title}"


def _format_boss(boss_id: str, boss_names: dict[str, str]) -> str:
    """форматирует босса для админского отчёта"""
    name = boss_names.get(boss_id, "неизвестный босс")
    return f"{boss_id} - {name}"


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
