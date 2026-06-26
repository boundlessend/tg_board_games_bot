import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from constants import CB_SETTINGS, CB_SETTINGS_TOGGLE_CYCLE
from database import DatabaseError, SQLiteHistoryStorage
from handlers.ui import edit_menu
from keyboards import create_settings_keyboard

logger = logging.getLogger(__name__)

SETTINGS_TEXT = (
    "Настройки.\n\n"
    "Авто-цикл словесных игр: при «вкл» после исчерпания слов круг "
    "начинается заново; при «выкл» нужно нажать «Новая игра»."
)


def create_settings_router(storage: SQLiteHistoryStorage) -> Router:
    """создаёт роутер меню настроек пользователя"""
    router = Router()

    @router.callback_query(F.data == CB_SETTINGS)
    async def handle_open_settings(callback: CallbackQuery) -> None:
        """показывает меню настроек"""
        await _show_settings(callback, storage)

    @router.callback_query(F.data == CB_SETTINGS_TOGGLE_CYCLE)
    async def handle_toggle_cycle(callback: CallbackQuery) -> None:
        """переключает авто-цикл словесных игр"""
        telegram_id = callback.from_user.id
        try:
            current = await storage.get_user_auto_cycle(telegram_id)
            await storage.set_user_auto_cycle(telegram_id, not current)
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"telegram_id": telegram_id, "action": "toggle_cycle"},
            )
            await callback.answer(
                "Не удалось сохранить настройку.", show_alert=True
            )
            return

        await _show_settings(callback, storage)

    return router


async def _show_settings(
    callback: CallbackQuery, storage: SQLiteHistoryStorage
) -> None:
    """перерисовывает меню настроек с актуальным состоянием"""
    try:
        auto_cycle = await storage.get_user_auto_cycle(callback.from_user.id)
    except DatabaseError:
        logger.exception(
            "database_error",
            extra={
                "telegram_id": callback.from_user.id,
                "action": "open_settings",
            },
        )
        await callback.answer(
            "Не удалось открыть настройки.", show_alert=True
        )
        return

    await edit_menu(callback, SETTINGS_TEXT, create_settings_keyboard(auto_cycle))
