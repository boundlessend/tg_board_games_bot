from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from constants import (
    ADMIN_ACTIVITY_TITLE,
    ADMIN_CLOSE_TITLE,
    ADMIN_CSV_TITLE,
    ADMIN_STATS_TITLE,
    BACK_TO_DANGEROUS_WORDS_TITLE,
    BACK_TO_MAIN_MENU_TITLE,
    BUNKER_CANCEL_TITLE,
    BUNKER_GAME_TITLE,
    BUNKER_JOIN_TITLE,
    BUNKER_MODE_BASE_TITLE,
    BUNKER_MODE_STORY_TITLE,
    BUNKER_NEXT_TITLE,
    BUNKER_REVEAL_TITLE,
    BUNKER_START_TITLE,
    BUNKER_STORY_NO_TITLE,
    BUNKER_STORY_YES_TITLE,
    BUNKER_VOTE_START_TITLE,
    BUNKER_VOTE_TALLY_TITLE,
    CB_ADMIN_ACTIVITY,
    CB_ADMIN_CLOSE,
    CB_ADMIN_CSV,
    CB_ADMIN_STATS,
    CB_BK_CANCEL,
    CB_BK_JOIN,
    CB_BK_NEXT,
    CB_BK_OPEN,
    CB_BK_REVEAL,
    CB_BK_MODE,
    CB_BK_SOLO_CANCEL,
    CB_BK_SOLO_START,
    CB_BK_START,
    CB_BK_STORY_NO,
    CB_BK_STORY_TALLY,
    CB_BK_STORY_YES,
    CB_BK_VOTE_PREFIX,
    CB_BK_VOTE_START,
    CB_BK_VOTE_TALLY,
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
    CB_GS_TEAMS_PREFIX,
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
    MAX_TEAMS,
    MIN_TEAMS,
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
    WORD_GAME_GET_TITLE,
    WORD_GAME_RESET_TITLE,
    team_label,
)
from services.random_generator import WordGame


def create_private_menu_keyboard(
    word_games: list[WordGame],
) -> InlineKeyboardMarkup:
    """создаёт меню личного чата: «Кто я» и настройки"""
    rows = [
        [
            InlineKeyboardButton(
                text=game.title,
                callback_data=CB_WG_OPEN_PREFIX + game.game_id,
            )
        ]
        for game in word_games
        if game.game_id == "whoami"
    ]
    rows.append(
        [InlineKeyboardButton(text=SETTINGS_TITLE, callback_data=CB_SETTINGS)]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def create_group_menu_keyboard(
    word_games: list[WordGame],
) -> InlineKeyboardMarkup:
    """создаёт меню беседы: командные словесные игры, опасные слова и бункер"""
    rows = [
        [
            InlineKeyboardButton(
                text=game.title,
                callback_data=CB_GS_NEW_PREFIX + game.game_id,
            )
        ]
        for game in word_games
        if game.game_id != "whoami"
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=DANGEROUS_WORDS_GAME_TITLE, callback_data=CB_DW_ROLES
            )
        ]
    )
    rows.append(
        [InlineKeyboardButton(text=BUNKER_GAME_TITLE, callback_data=CB_BK_OPEN)]
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


def create_session_lobby_keyboard(team_count: int) -> InlineKeyboardMarkup:
    """создаёт клавиатуру лобби: выбор числа команд и вступление"""
    count_row = [
        InlineKeyboardButton(
            text=(f"[{number}]" if number == team_count else str(number)),
            callback_data=CB_GS_TEAMS_PREFIX + str(number),
        )
        for number in range(MIN_TEAMS, MAX_TEAMS + 1)
    ]
    rows = [count_row]
    rows.extend(
        [
            InlineKeyboardButton(
                text=f"Вступить: {team_label(index)}",
                callback_data=CB_GS_JOIN_PREFIX + str(index),
            )
        ]
        for index in range(team_count)
    )
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


def create_bunker_lobby_keyboard(story_mode: bool) -> InlineKeyboardMarkup:
    """создаёт клавиатуру лобби игры бункер с переключателем режима"""
    mode_title = BUNKER_MODE_STORY_TITLE if story_mode else BUNKER_MODE_BASE_TITLE
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BUNKER_JOIN_TITLE, callback_data=CB_BK_JOIN
                )
            ],
            [InlineKeyboardButton(text=mode_title, callback_data=CB_BK_MODE)],
            [
                InlineKeyboardButton(
                    text=BUNKER_START_TITLE, callback_data=CB_BK_START
                ),
                InlineKeyboardButton(
                    text=BUNKER_CANCEL_TITLE, callback_data=CB_BK_CANCEL
                ),
            ],
        ]
    )


def create_bunker_story_keyboard() -> InlineKeyboardMarkup:
    """создаёт клавиатуру голосования в финале «история выживания»"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BUNKER_STORY_YES_TITLE, callback_data=CB_BK_STORY_YES
                ),
                InlineKeyboardButton(
                    text=BUNKER_STORY_NO_TITLE, callback_data=CB_BK_STORY_NO
                ),
            ],
            [
                InlineKeyboardButton(
                    text=BUNKER_VOTE_TALLY_TITLE, callback_data=CB_BK_STORY_TALLY
                )
            ],
            [
                InlineKeyboardButton(
                    text=BUNKER_CANCEL_TITLE, callback_data=CB_BK_CANCEL
                )
            ],
        ]
    )


def create_bunker_reveal_keyboard(votes_pending: bool) -> InlineKeyboardMarkup:
    """создаёт клавиатуру фазы открытия карт игры бункер"""
    control = (
        InlineKeyboardButton(
            text=BUNKER_VOTE_START_TITLE, callback_data=CB_BK_VOTE_START
        )
        if votes_pending
        else InlineKeyboardButton(
            text=BUNKER_NEXT_TITLE, callback_data=CB_BK_NEXT
        )
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BUNKER_REVEAL_TITLE, callback_data=CB_BK_REVEAL
                )
            ],
            [control],
            [
                InlineKeyboardButton(
                    text=BUNKER_CANCEL_TITLE, callback_data=CB_BK_CANCEL
                )
            ],
        ]
    )


def create_bunker_solo_lobby_keyboard() -> InlineKeyboardMarkup:
    """создаёт клавиатуру лобби режима «отдельно» игры бункер"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BUNKER_START_TITLE, callback_data=CB_BK_SOLO_START
                ),
                InlineKeyboardButton(
                    text=BUNKER_CANCEL_TITLE, callback_data=CB_BK_SOLO_CANCEL
                ),
            ]
        ]
    )


def create_bunker_vote_keyboard(
    candidates: list[tuple[int, str]],
) -> InlineKeyboardMarkup:
    """создаёт клавиатуру голосования за изгнание в игре бункер"""
    rows = [
        [
            InlineKeyboardButton(
                text=name, callback_data=CB_BK_VOTE_PREFIX + str(candidate_id)
            )
        ]
        for candidate_id, name in candidates
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=BUNKER_VOTE_TALLY_TITLE, callback_data=CB_BK_VOTE_TALLY
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=BUNKER_CANCEL_TITLE, callback_data=CB_BK_CANCEL
            )
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
