from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from keyboards import BACK_TO_MAIN_MENU_TITLE, create_main_menu_keyboard

start_router = Router()


MAIN_MENU_TEXT = "Ку, сначала выбери игру:"


@start_router.message(CommandStart())
async def handle_start(message: Message) -> None:
    """показывает главное меню после команды start"""
    await message.answer(
        MAIN_MENU_TEXT, reply_markup=create_main_menu_keyboard()
    )


@start_router.message(F.text == BACK_TO_MAIN_MENU_TITLE)
async def handle_back_to_main_menu(message: Message) -> None:
    """возвращает пользователя в главное меню"""
    await message.answer(
        MAIN_MENU_TEXT, reply_markup=create_main_menu_keyboard()
    )
