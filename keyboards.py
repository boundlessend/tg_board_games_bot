from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from constants import (
    ADMIN_ACTIVITY_TITLE,
    ADMIN_CLOSE_TITLE,
    ADMIN_CSV_TITLE,
    ADMIN_STATS_TITLE,
    BACK_TO_DANGEROUS_WORDS_TITLE,
    BACK_TO_MAIN_MENU_TITLE,
    CB_ADMIN_ACTIVITY,
    CB_ADMIN_CLOSE,
    CB_ADMIN_CSV,
    CB_ADMIN_STATS,
    CB_DW_BOSS,
    CB_DW_CURSE,
    CB_DW_HOST,
    CB_DW_PLAYER,
    CB_DW_NEW_GAME,
    CB_DW_RESET_BOSSES,
    CB_DW_RESET_CURSES,
    CB_DW_RESET_WORDS,
    CB_DW_ROLES,
    CB_DW_WORD,
    CB_GS_CANCEL,
    CB_GS_FINISH,
    CB_GS_JOIN_PREFIX,
    CB_GS_NEW_PREFIX,
    CB_GS_NEXT,
    CB_GS_SCORE,
    CB_GS_SKIP,
    CB_GS_START,
    CB_GS_WORD,
    CB_MAIN_MENU,
    CB_SETTINGS,
    CB_SETTINGS_TOGGLE_CYCLE,
    CB_WG_OPEN_PREFIX,
    CB_WG_RESET_PREFIX,
    CB_WG_WORD_PREFIX,
    CREATE_BOSS_TITLE,
    CREATE_CURSE_TITLE,
    CREATE_WORD_TITLE,
    DANGEROUS_WORDS_GAME_TITLE,
    DANGEROUS_WORDS_HOST_TITLE,
    DANGEROUS_WORDS_PLAYER_TITLE,
    NEW_GAME_TITLE,
    RESET_BOSSES_TITLE,
    RESET_CURSES_TITLE,
    RESET_WORDS_TITLE,
    SESSION_CANCEL_TITLE,
    SESSION_FINISH_TITLE,
    SESSION_NEXT_TITLE,
    SESSION_SCORE_TITLE,
    SESSION_SKIP_TITLE,
    SESSION_START_TITLE,
    SESSION_WORD_TITLE,
    SETTINGS_TITLE,
    TEAM_NAMES,
    WORD_GAME_GET_TITLE,
    WORD_GAME_RESET_TITLE,
)
from services.random_generator import WordGame


def create_main_menu_keyboard(
    word_games: list[WordGame],
) -> InlineKeyboardMarkup:
    """создаёт inline-клавиатуру главного меню со списком игр"""
    rows = [
        [
            InlineKeyboardButton(
                text=DANGEROUS_WORDS_GAME_TITLE, callback_data=CB_DW_ROLES
            )
        ]
    ]
    for game in word_games:
        rows.append(
            [
                InlineKeyboardButton(
                    text=game.title,
                    callback_data=CB_WG_OPEN_PREFIX + game.game_id,
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text=SETTINGS_TITLE, callback_data=CB_SETTINGS)]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def create_settings_keyboard(auto_cycle: bool) -> InlineKeyboardMarkup:
    """создаёт inline-клавиатуру меню настроек"""
    state = "вкл" if auto_cycle else "выкл"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Авто-цикл словесных игр: {state}",
                    callback_data=CB_SETTINGS_TOGGLE_CYCLE,
                )
            ],
            [
                InlineKeyboardButton(
                    text=BACK_TO_MAIN_MENU_TITLE, callback_data=CB_MAIN_MENU
                )
            ],
        ]
    )


def create_word_game_keyboard(game_id: str) -> InlineKeyboardMarkup:
    """создаёт inline-клавиатуру словесной игры"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=WORD_GAME_GET_TITLE,
                    callback_data=CB_WG_WORD_PREFIX + game_id,
                )
            ],
            [
                InlineKeyboardButton(
                    text=WORD_GAME_RESET_TITLE,
                    callback_data=CB_WG_RESET_PREFIX + game_id,
                )
            ],
            [
                InlineKeyboardButton(
                    text=BACK_TO_MAIN_MENU_TITLE, callback_data=CB_MAIN_MENU
                )
            ],
        ]
    )


def create_dangerous_words_role_keyboard() -> InlineKeyboardMarkup:
    """создаёт inline-клавиатуру выбора роли в помощнике опасные слова"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=DANGEROUS_WORDS_HOST_TITLE, callback_data=CB_DW_HOST
                ),
                InlineKeyboardButton(
                    text=DANGEROUS_WORDS_PLAYER_TITLE,
                    callback_data=CB_DW_PLAYER,
                ),
            ],
            [
                InlineKeyboardButton(
                    text=BACK_TO_MAIN_MENU_TITLE, callback_data=CB_MAIN_MENU
                )
            ],
        ]
    )


def create_dangerous_words_host_keyboard() -> InlineKeyboardMarkup:
    """создаёт inline-клавиатуру ведущего в помощнике опасные слова"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=CREATE_WORD_TITLE, callback_data=CB_DW_WORD
                )
            ],
            [
                InlineKeyboardButton(
                    text=RESET_WORDS_TITLE, callback_data=CB_DW_RESET_WORDS
                )
            ],
            [
                InlineKeyboardButton(
                    text=CREATE_CURSE_TITLE, callback_data=CB_DW_CURSE
                )
            ],
            [
                InlineKeyboardButton(
                    text=RESET_CURSES_TITLE, callback_data=CB_DW_RESET_CURSES
                )
            ],
            [
                InlineKeyboardButton(
                    text=CREATE_BOSS_TITLE, callback_data=CB_DW_BOSS
                )
            ],
            [
                InlineKeyboardButton(
                    text=RESET_BOSSES_TITLE, callback_data=CB_DW_RESET_BOSSES
                )
            ],
            [
                InlineKeyboardButton(
                    text=NEW_GAME_TITLE, callback_data=CB_DW_NEW_GAME
                )
            ],
            [
                InlineKeyboardButton(
                    text=BACK_TO_DANGEROUS_WORDS_TITLE,
                    callback_data=CB_DW_ROLES,
                )
            ],
        ]
    )


def create_dangerous_words_player_keyboard() -> InlineKeyboardMarkup:
    """создаёт inline-клавиатуру игрока в помощнике опасные слова"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=CREATE_WORD_TITLE, callback_data=CB_DW_WORD
                )
            ],
            [
                InlineKeyboardButton(
                    text=RESET_WORDS_TITLE, callback_data=CB_DW_RESET_WORDS
                )
            ],
            [
                InlineKeyboardButton(
                    text=NEW_GAME_TITLE, callback_data=CB_DW_NEW_GAME
                )
            ],
            [
                InlineKeyboardButton(
                    text=BACK_TO_DANGEROUS_WORDS_TITLE,
                    callback_data=CB_DW_ROLES,
                )
            ],
        ]
    )


def create_admin_keyboard() -> InlineKeyboardMarkup:
    """создаёт inline-клавиатуру админ-меню"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=ADMIN_STATS_TITLE, callback_data=CB_ADMIN_STATS
                )
            ],
            [
                InlineKeyboardButton(
                    text=ADMIN_CSV_TITLE, callback_data=CB_ADMIN_CSV
                )
            ],
            [
                InlineKeyboardButton(
                    text=ADMIN_ACTIVITY_TITLE, callback_data=CB_ADMIN_ACTIVITY
                )
            ],
            [
                InlineKeyboardButton(
                    text=ADMIN_CLOSE_TITLE, callback_data=CB_ADMIN_CLOSE
                )
            ],
        ]
    )


def create_play_games_keyboard(
    word_games: list[WordGame],
) -> InlineKeyboardMarkup:
    """создаёт клавиатуру выбора игры для групповой сессии"""
    rows = [
        [
            InlineKeyboardButton(
                text=game.title,
                callback_data=CB_GS_NEW_PREFIX + game.game_id,
            )
        ]
        for game in word_games
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def create_session_lobby_keyboard() -> InlineKeyboardMarkup:
    """создаёт клавиатуру лобби групповой сессии"""
    rows = [
        [
            InlineKeyboardButton(
                text=f"Вступить: {name}",
                callback_data=CB_GS_JOIN_PREFIX + str(index),
            )
        ]
        for index, name in enumerate(TEAM_NAMES)
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=SESSION_START_TITLE, callback_data=CB_GS_START
            ),
            InlineKeyboardButton(
                text=SESSION_CANCEL_TITLE, callback_data=CB_GS_CANCEL
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def create_session_play_keyboard() -> InlineKeyboardMarkup:
    """создаёт клавиатуру управления групповой сессией"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=SESSION_WORD_TITLE, callback_data=CB_GS_WORD
                )
            ],
            [
                InlineKeyboardButton(
                    text=SESSION_SCORE_TITLE, callback_data=CB_GS_SCORE
                ),
                InlineKeyboardButton(
                    text=SESSION_SKIP_TITLE, callback_data=CB_GS_SKIP
                ),
            ],
            [
                InlineKeyboardButton(
                    text=SESSION_NEXT_TITLE, callback_data=CB_GS_NEXT
                )
            ],
            [
                InlineKeyboardButton(
                    text=SESSION_FINISH_TITLE, callback_data=CB_GS_FINISH
                )
            ],
        ]
    )
