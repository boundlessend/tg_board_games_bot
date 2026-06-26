from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from constants import (
    ADMIN_ACTIVITY_TITLE,
    ADMIN_CLOSE_TITLE,
    ADMIN_CSV_TITLE,
    ADMIN_STATS_TITLE,
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
    CB_DG_BOSS,
    CB_DG_CURSE,
    CB_DG_EXPLAIN,
    CB_DG_FINISH,
    CB_DG_NEXT,
    CB_DG_OPEN,
    CB_DG_SEND,
    CB_DG_WORD,
    CB_GS_CANCEL,
    CB_GS_FINISH,
    CB_GS_JOIN_PREFIX,
    CB_GS_NEW_PREFIX,
    CB_GS_NEXT,
    CB_GS_REROLL,
    CB_GS_SCORE,
    CB_GS_SKIP,
    CB_GS_START,
    CB_GS_TEAMS_PREFIX,
    CB_GS_TIMER_PREFIX,
    CB_GS_WORD,
    CB_MAIN_MENU,
    CB_SETTINGS,
    CB_SETTINGS_TOGGLE_CYCLE,
    CB_WG_OPEN_PREFIX,
    CB_WG_RESET_PREFIX,
    CB_WG_WORD_PREFIX,
    DANGEROUS_WORDS_GAME_TITLE,
    DG_BOSS_TITLE,
    DG_CURSE_TITLE,
    DG_EXPLAIN_TITLE,
    DG_FINISH_TITLE,
    DG_KEEP_TITLE,
    DG_NEXT_TITLE,
    DG_REROLL_TITLE,
    DG_SEND_TITLE,
    DG_WORD_TITLE,
    MAX_TEAMS,
    MIN_TEAMS,
    SESSION_CANCEL_TITLE,
    SESSION_FINISH_TITLE,
    SESSION_NEXT_TITLE,
    SESSION_REROLL_TITLE,
    SESSION_SCORE_TITLE,
    SESSION_SKIP_TITLE,
    SESSION_START_TITLE,
    SESSION_WORD_TITLE,
    SETTINGS_TITLE,
    TURN_SECONDS_OPTIONS,
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
                text=DANGEROUS_WORDS_GAME_TITLE, callback_data=CB_DG_OPEN
            )
        ]
    )
    rows.append(
        [InlineKeyboardButton(text=BUNKER_GAME_TITLE, callback_data=CB_BK_OPEN)]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def create_dangerous_group_keyboard() -> InlineKeyboardMarkup:
    """создаёт клавиатуру командной партии «опасные слова» в беседе"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=DG_EXPLAIN_TITLE, callback_data=CB_DG_EXPLAIN
                )
            ],
            [
                InlineKeyboardButton(
                    text=DG_WORD_TITLE, callback_data=CB_DG_WORD
                ),
                InlineKeyboardButton(
                    text=DG_SEND_TITLE, callback_data=CB_DG_SEND
                ),
            ],
            [
                InlineKeyboardButton(
                    text=DG_NEXT_TITLE, callback_data=CB_DG_NEXT
                )
            ],
            [
                InlineKeyboardButton(
                    text=DG_CURSE_TITLE, callback_data=CB_DG_CURSE
                ),
                InlineKeyboardButton(
                    text=DG_BOSS_TITLE, callback_data=CB_DG_BOSS
                ),
            ],
            [
                InlineKeyboardButton(
                    text=DG_FINISH_TITLE, callback_data=CB_DG_FINISH
                )
            ],
        ]
    )


def create_dg_offer_keyboard(
    keep_data: str, reroll_data: str
) -> InlineKeyboardMarkup:
    """создаёт клавиатуру «принять/реролл» для проклятия или босса"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=DG_KEEP_TITLE, callback_data=keep_data
                ),
                InlineKeyboardButton(
                    text=DG_REROLL_TITLE, callback_data=reroll_data
                ),
            ]
        ]
    )


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


def create_session_lobby_keyboard(
    team_count: int, turn_seconds: int
) -> InlineKeyboardMarkup:
    """создаёт клавиатуру лобби: число команд, время хода и вступление"""
    count_row = [
        InlineKeyboardButton(
            text=(f"[{number}]" if number == team_count else str(number)),
            callback_data=CB_GS_TEAMS_PREFIX + str(number),
        )
        for number in range(MIN_TEAMS, MAX_TEAMS + 1)
    ]
    timer_row = [
        InlineKeyboardButton(
            text=(f"[{sec}с]" if sec == turn_seconds else f"{sec}с"),
            callback_data=CB_GS_TIMER_PREFIX + str(sec),
        )
        for sec in TURN_SECONDS_OPTIONS
    ]
    rows = [count_row, timer_row]
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
                    text=SESSION_REROLL_TITLE, callback_data=CB_GS_REROLL
                )
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
