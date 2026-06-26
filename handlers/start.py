from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from constants import CB_MAIN_MENU
from keyboards import (
    create_group_menu_keyboard,
    create_private_menu_keyboard,
)
from services.random_generator import WordGame

MAIN_MENU_TEXT = "Ку, сначала выбери игру:"

HELP_TEXT = (
    "Бот-помощник для настольных игр.\n\n"
    "В личке с ботом доступна игра «Кто я?»: тяни персонажа кнопкой.\n"
    "В беседе (добавь бота в группу) доступны «Крокодил», «Алиас», "
    "«Бункер» и «Опасные слова». В «Крокодиле» и «Алиасе» игроки делятся "
    "на команды (число команд и время хода настраиваются в лобби), "
    "в «Опасных словах» - две команды.\n\n"
    "Команды:\n"
    "/start - открыть меню\n"
    "/help - показать эту справку\n"
    "/fav - сохранить последнее слово в избранное\n"
    "/favorites - показать избранное\n"
    "/bunker - открыть «Бункер» в беседе\n\n"
    "Инлайн-режим: наберите @имя_бота в любом чате, чтобы получить "
    "случайные слова."
)


def create_start_router(word_games: list[WordGame]) -> Router:
    """создаёт роутер главного меню и справки"""
    router = Router()

    @router.message(CommandStart())
    async def handle_start(message: Message) -> None:
        """показывает меню по типу чата после команды start"""
        await message.answer(
            MAIN_MENU_TEXT,
            reply_markup=_menu_for_chat(word_games, message.chat.type),
        )

    @router.message(Command("help"))
    async def handle_help(message: Message) -> None:
        """показывает справку по боту"""
        await message.answer(
            HELP_TEXT,
            reply_markup=_menu_for_chat(word_games, message.chat.type),
        )

    @router.callback_query(F.data == CB_MAIN_MENU)
    async def handle_back_to_main_menu(callback: CallbackQuery) -> None:
        """возвращает пользователя в меню его чата"""
        message = callback.message
        if isinstance(message, Message):
            try:
                await message.edit_text(
                    MAIN_MENU_TEXT,
                    reply_markup=_menu_for_chat(
                        word_games, message.chat.type
                    ),
                )
            except TelegramBadRequest:
                pass
        await callback.answer()

    return router


def _menu_for_chat(
    word_games: list[WordGame], chat_type: str
) -> InlineKeyboardMarkup:
    """выбирает клавиатуру меню по типу чата"""
    if chat_type == "private":
        return create_private_menu_keyboard(word_games)
    return create_group_menu_keyboard(word_games)
