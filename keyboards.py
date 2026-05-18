from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from constants import (
    ADMIN_CLOSE_TITLE,
    ADMIN_STATS_TITLE,
    BACK_TO_DANGEROUS_WORDS_TITLE,
    BACK_TO_MAIN_MENU_TITLE,
    CREATE_BOSS_TITLE,
    CREATE_CURSE_TITLE,
    CREATE_WORD_TITLE,
    DANGEROUS_WORDS_GAME_TITLE,
    DANGEROUS_WORDS_HOST_TITLE,
    DANGEROUS_WORDS_PLAYER_TITLE,
    RESET_BOSSES_TITLE,
    RESET_CURSES_TITLE,
    RESET_WORDS_TITLE,
)


def create_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """создаёт клавиатуру главного меню"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=DANGEROUS_WORDS_GAME_TITLE)],
        ],
        resize_keyboard=True,
    )


def create_dangerous_words_role_keyboard() -> ReplyKeyboardMarkup:
    """создаёт клавиатуру выбора роли в помощнике опасные слова"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=DANGEROUS_WORDS_HOST_TITLE),
                KeyboardButton(text=DANGEROUS_WORDS_PLAYER_TITLE),
            ],
            [KeyboardButton(text=BACK_TO_MAIN_MENU_TITLE)],
        ],
        resize_keyboard=True,
    )


def create_dangerous_words_host_keyboard() -> ReplyKeyboardMarkup:
    """создаёт клавиатуру ведущего в помощнике опасные слова"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=CREATE_WORD_TITLE)],
            [KeyboardButton(text=RESET_WORDS_TITLE)],
            [KeyboardButton(text=CREATE_CURSE_TITLE)],
            [KeyboardButton(text=RESET_CURSES_TITLE)],
            [KeyboardButton(text=CREATE_BOSS_TITLE)],
            [KeyboardButton(text=RESET_BOSSES_TITLE)],
            [KeyboardButton(text=BACK_TO_DANGEROUS_WORDS_TITLE)],
        ],
        resize_keyboard=True,
    )


def create_dangerous_words_player_keyboard() -> ReplyKeyboardMarkup:
    """создаёт клавиатуру игрока в помощнике опасные слова"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=CREATE_WORD_TITLE)],
            [KeyboardButton(text=RESET_WORDS_TITLE)],
            [KeyboardButton(text=BACK_TO_DANGEROUS_WORDS_TITLE)],
        ],
        resize_keyboard=True,
    )


def create_admin_keyboard() -> ReplyKeyboardMarkup:
    """создаёт клавиатуру секретного админ-меню"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ADMIN_STATS_TITLE)],
            [KeyboardButton(text=ADMIN_CLOSE_TITLE)],
        ],
        resize_keyboard=True,
    )
