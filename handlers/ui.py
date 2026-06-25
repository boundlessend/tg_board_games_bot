from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message


async def edit_menu(
    callback: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup
) -> None:
    """меняет текст и клавиатуру сообщения при навигации по меню"""
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text, reply_markup=keyboard)
        except TelegramBadRequest:
            pass
    await callback.answer()


async def edit_result(callback: CallbackQuery, text: str) -> None:
    """показывает результат выдачи, сохраняя текущую клавиатуру меню"""
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text, reply_markup=message.reply_markup)
        except TelegramBadRequest:
            pass
    await callback.answer()
