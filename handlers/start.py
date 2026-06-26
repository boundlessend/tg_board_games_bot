from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from constants import CB_MAIN_MENU
from keyboards import create_main_menu_keyboard
from services.random_generator import WordGame

MAIN_MENU_TEXT = "Ку, сначала выбери игру:"

HELP_TEXT = (
    "Бот-помощник для настольных игр.\n\n"
    "Управление кнопками меню. Команды:\n"
    "/start - открыть главное меню\n"
    "/help - показать эту справку\n"
    "/fav - сохранить последнее слово в избранное\n"
    "/favorites - показать избранное\n"
    "/play - групповая сессия с командами (в групповом чате)\n"
    "/bunker - игра «Бункер» (в группе - автопартия, в личке - режим «отдельно»)\n"
    "/joinbunker КОД - войти в «Бункер» по коду (в личке)\n\n"
    "Игры:\n"
    "«Опасные слова» - слова, проклятья и боссы (роль ведущего) или только "
    "слова (роль игрока).\n"
    "«Крокодил», «Алиас» - случайные слова для объяснения.\n"
    "Кнопки «Новая игра» сбрасывают историю выдач.\n\n"
    "Инлайн-режим: наберите @имя_бота в любом чате, чтобы получить "
    "случайные слова."
)


def create_start_router(word_games: list[WordGame]) -> Router:
    """создаёт роутер главного меню и справки"""
    router = Router()

    @router.message(CommandStart())
    async def handle_start(message: Message) -> None:
        """показывает главное меню после команды start"""
        await message.answer(
            MAIN_MENU_TEXT, reply_markup=create_main_menu_keyboard(word_games)
        )

    @router.message(Command("help"))
    async def handle_help(message: Message) -> None:
        """показывает справку по боту"""
        await message.answer(
            HELP_TEXT, reply_markup=create_main_menu_keyboard(word_games)
        )

    @router.callback_query(F.data == CB_MAIN_MENU)
    async def handle_back_to_main_menu(callback: CallbackQuery) -> None:
        """возвращает пользователя в главное меню"""
        message = callback.message
        if isinstance(message, Message):
            try:
                await message.edit_text(
                    MAIN_MENU_TEXT,
                    reply_markup=create_main_menu_keyboard(word_games),
                )
            except TelegramBadRequest:
                pass
        await callback.answer()

    return router
