import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from constants import (
    CB_ADMIN_ACTIVITY,
    CB_ADMIN_CLOSE,
    CB_ADMIN_CSV,
    CB_ADMIN_STATS,
)
from database import (
    DatabaseError,
    SQLiteHistoryStorage,
    SummaryTotals,
    iso_days_ago,
)
from handlers.common import is_private_admin, is_private_admin_callback
from keyboards import create_admin_keyboard, create_private_menu_keyboard
from services.content import WordGame

logger = logging.getLogger(__name__)

ADMIN_CLOSED_TEXT = "Админка закрыта"
TOP_WORDS_LIMIT = 10
# выгрузки содержат telegram_id и остаются на серверах telegram
ID_WARNING = "В файле есть telegram_id пользователей: он останется в Telegram."
CSV_CAPTION = f"Статистика CSV.\n{ID_WARNING}"


def create_admin_router(
    storage: SQLiteHistoryStorage,
    admin_ids: frozenset[int],
    word_games: list[WordGame],
) -> Router:
    """создаёт роутер админ-меню с доступом по telegram id"""
    router = Router()

    def _is_admin_message(message: Message) -> bool:
        return is_private_admin(message, admin_ids)

    def _is_admin_callback(callback: CallbackQuery) -> bool:
        return is_private_admin_callback(callback, admin_ids)

    router.message.filter(_is_admin_message)
    router.callback_query.filter(_is_admin_callback)

    @router.message(Command("admin"))
    async def handle_admin_open(message: Message) -> None:
        user = message.from_user
        if user is None:
            return
        await _send_summary(message, storage, user.id)

    @router.callback_query(F.data == CB_ADMIN_STATS)
    async def handle_admin_stats_request(callback: CallbackQuery) -> None:
        message = callback.message
        if isinstance(message, Message):
            await _send_all_statistics(
                message, word_games, storage, callback.from_user.id
            )
        await callback.answer()

    @router.callback_query(F.data == CB_ADMIN_CSV)
    async def handle_admin_csv(callback: CallbackQuery) -> None:
        message = callback.message
        if not isinstance(message, Message):
            await callback.answer()
            return

        try:
            statistics = await storage.get_game_word_statistics()
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={
                    "telegram_id": callback.from_user.id,
                    "action": "admin_csv",
                },
            )
            await callback.answer("Не удалось получить статистику.", show_alert=True)
            return

        document = BufferedInputFile(
            _build_statistics_csv(statistics).encode("utf-8"),
            filename="stats.csv",
        )
        await message.answer_document(document, caption=CSV_CAPTION)
        await callback.answer()

    @router.callback_query(F.data == CB_ADMIN_ACTIVITY)
    async def handle_admin_activity(callback: CallbackQuery) -> None:
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
            await callback.answer("Не удалось получить активность.", show_alert=True)
            return

        await message.answer(
            _format_activity(by_day), reply_markup=create_admin_keyboard()
        )
        await callback.answer()

    @router.callback_query(F.data == CB_ADMIN_CLOSE)
    async def handle_admin_close(callback: CallbackQuery) -> None:
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


async def _send_summary(
    message: Message,
    storage: SQLiteHistoryStorage,
    admin_id: int,
) -> None:
    """отправляет администратору сводку по всем пользователям"""
    try:
        totals = await storage.get_summary_totals()
        top_words = await storage.get_top_game_words(TOP_WORDS_LIMIT)
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
        _build_summary(totals, top_words, recent_issuances, recent_users),
        reply_markup=create_admin_keyboard(),
    )


def _build_summary(
    totals: SummaryTotals,
    top_words: list[tuple[str, int]],
    recent_issuances: int,
    recent_users: int,
) -> str:
    """собирает сводку по всем пользователям

    считаются словесные игры: только их выдачи привязаны к telegram_id.
    В групповых играх контент выдаётся на партию, а не на человека, и
    персональной истории по нему нет
    """
    if totals.users == 0:
        return "Статистика пока пустая."

    sections = [
        "Сводка (словесные игры)",
        f"Пользователей: {totals.users}",
        f"Выданных слов: {totals.game_words}",
        f"Выдач за 7 дней: {recent_issuances}",
        f"Активных за 7 дней: {recent_users}",
        f"Топ-{TOP_WORDS_LIMIT} слов:",
        _format_values([f"{word} x{count}" for word, count in top_words]),
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
    """собирает csv с числом выданных слов по пользователю и игре"""
    lines = ["telegram_id,game_id,words"]
    for telegram_id, by_game in statistics.items():
        for game_id, words in by_game.items():
            lines.append(f"{telegram_id},{game_id},{len(words)}")
    return "\n".join(lines) + "\n"


async def _send_all_statistics(
    message: Message,
    word_games: list[WordGame],
    storage: SQLiteHistoryStorage,
    admin_id: int,
) -> None:
    """отправляет подробный отчёт одним файлом

    отчёт растёт линейно с числом пользователей, а серия сообщений упёрлась
    бы во флуд-контроль telegram, поэтому отдаём документом
    """
    try:
        statistics = await storage.get_game_word_statistics()
    except DatabaseError:
        logger.exception(
            "database_error",
            extra={"telegram_id": admin_id, "action": "admin_all_stats"},
        )
        await message.answer("Не удалось получить статистику.")
        return

    report = _build_all_statistics_report(word_games, statistics)
    document = BufferedInputFile(report.encode("utf-8"), filename="report.txt")
    await message.answer_document(
        document,
        caption=f"Полный отчёт: пользователей {len(statistics)}.\n{ID_WARNING}",
        reply_markup=create_admin_keyboard(),
    )


def _build_all_statistics_report(
    word_games: list[WordGame],
    statistics: dict[int, dict[str, list[str]]],
) -> str:
    """собирает общий отчёт по всем пользователям"""
    if len(statistics) == 0:
        return "Статистика пока пустая."

    pool_sizes = {game.game_id: len(game.words) for game in word_games}
    reports = [
        _build_user_statistics_report(pool_sizes, telegram_id, user_statistics)
        for telegram_id, user_statistics in statistics.items()
    ]
    return "\n\n---\n\n".join(reports)


def _build_user_statistics_report(
    pool_sizes: dict[str, int],
    telegram_id: int,
    statistics: dict[str, list[str]],
) -> str:
    """собирает текстовый отчёт по истории слов пользователя"""
    sections = [f"Статистика пользователя {telegram_id}"]
    for game_id, words in statistics.items():
        pool_size = pool_sizes.get(game_id)
        counter = f"{len(words)}/{pool_size}" if pool_size else str(len(words))
        sections.append(f"{game_id}: {counter}")
        sections.append(_format_values(words))
    return "\n\n".join(sections)


def _format_values(values: list[str]) -> str:
    """форматирует список значений для отчёта"""
    if len(values) == 0:
        return "пока пусто"
    return "\n".join(values)
