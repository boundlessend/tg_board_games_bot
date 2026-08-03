import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from constants import (
    CB_FORGET_ME_NO,
    CB_FORGET_ME_YES,
    CB_SETTINGS,
    CB_SETTINGS_TOGGLE_CYCLE,
)
from database import DatabaseError, SQLiteHistoryStorage
from handlers.ui import edit_menu
from keyboards import create_forget_me_keyboard, create_settings_keyboard

logger = logging.getLogger(__name__)

SETTINGS_TEXT = (
    "Настройки.\n\n"
    "Авто-цикл словесных игр: при «вкл» после исчерпания слов круг "
    "начинается заново; при «выкл» нужно нажать «Новая игра»."
)

FORGET_ME_TEXT = (
    "Удалить все твои данные?\n\n"
    "Будут стёрты: история выданных слов, проклятий и боссов, избранное, "
    "настройки и последнее слово. Отменить это нельзя, история выдач "
    "начнётся с нуля."
)


def create_settings_router(storage: SQLiteHistoryStorage) -> Router:
    """создаёт роутер меню настроек и удаления личных данных"""
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
            await callback.answer("Не удалось сохранить настройку.", show_alert=True)
            return

        await _show_settings(callback, storage)

    @router.message(Command("forgetme"))
    async def handle_forget_me_request(message: Message) -> None:
        """спрашивает подтверждение перед удалением личных данных"""
        await message.answer(FORGET_ME_TEXT, reply_markup=create_forget_me_keyboard())

    @router.callback_query(F.data == CB_FORGET_ME_NO)
    async def handle_forget_me_cancel(callback: CallbackQuery) -> None:
        """отменяет удаление личных данных"""
        message = callback.message
        if isinstance(message, Message):
            await message.edit_text("Отменено, данные на месте.")
        await callback.answer()

    @router.callback_query(F.data == CB_FORGET_ME_YES)
    async def handle_forget_me(callback: CallbackQuery) -> None:
        """удаляет всю историю, избранное и настройки пользователя"""
        telegram_id = callback.from_user.id
        try:
            removed = await storage.delete_user_data(telegram_id)
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"telegram_id": telegram_id, "action": "forget_me"},
            )
            await callback.answer("Не удалось удалить данные.", show_alert=True)
            return

        message = callback.message
        if isinstance(message, Message):
            await message.edit_text(f"Готово, удалено записей: {removed}.")
        await callback.answer()

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
        await callback.answer("Не удалось открыть настройки.", show_alert=True)
        return

    await edit_menu(callback, SETTINGS_TEXT, create_settings_keyboard(auto_cycle))
