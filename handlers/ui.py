import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from handlers.common import is_not_modified

logger = logging.getLogger(__name__)


async def edit_menu(
    callback: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup
) -> None:
    """меняет текст и клавиатуру сообщения при навигации по меню"""
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text, reply_markup=keyboard)
        except TelegramBadRequest as error:
            if not is_not_modified(error):
                logger.warning(
                    "menu_edit_failed",
                    extra={"chat_id": message.chat.id, "error": str(error)},
                )
                raise
    await callback.answer()


async def edit_result(callback: CallbackQuery, text: str) -> None:
    """показывает результат выдачи, сохраняя текущую клавиатуру меню"""
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text, reply_markup=message.reply_markup)
        except TelegramBadRequest as error:
            if not is_not_modified(error):
                logger.warning(
                    "result_edit_failed",
                    extra={"chat_id": message.chat.id, "error": str(error)},
                )
                raise
    await callback.answer()
