ADMIN_ACTIVITY_TITLE = "Активность"
ADMIN_CLOSE_TITLE = "Закрыть админку"
ADMIN_CSV_TITLE = "Выгрузить CSV"
ADMIN_STATS_TITLE = "Полный отчёт"
BACK_TO_MAIN_MENU_TITLE = "Назад в главное меню"
DANGEROUS_WORDS_GAME_TITLE = "Опасные слова"
SETTINGS_TITLE = "Настройки"

CB_MAIN_MENU = "menu:main"
CB_ADMIN_CSV = "admin:csv"
CB_ADMIN_ACTIVITY = "admin:activity"

CB_SETTINGS = "settings:open"
CB_SETTINGS_TOGGLE_CYCLE = "settings:toggle_cycle"

CB_WG_OPEN_PREFIX = "wg:open:"
CB_WG_WORD_PREFIX = "wg:word:"
CB_WG_RESET_PREFIX = "wg:reset:"

CB_GS_NEW_PREFIX = "gs:new:"
CB_GS_JOIN_PREFIX = "gs:join:"
CB_GS_START = "gs:start"
CB_GS_CANCEL = "gs:cancel"
CB_GS_WORD = "gs:word"
CB_GS_SCORE = "gs:score"
CB_GS_SKIP = "gs:skip"
CB_GS_NEXT = "gs:next"
CB_GS_FINISH = "gs:finish"
CB_GS_TEAMS_PREFIX = "gs:teams:"
CB_GS_TIMER_PREFIX = "gs:timer:"

MIN_TEAMS = 2
MAX_TEAMS = 4
TURN_SECONDS_OPTIONS: tuple[int, ...] = (30, 60, 90)
DEFAULT_TURN_SECONDS = 60


def team_label(index: int) -> str:
    """имя команды по индексу (счёт с нуля)"""
    return f"Команда {index + 1}"


SESSION_START_TITLE = "Начать"
SESSION_CANCEL_TITLE = "Отмена"
SESSION_WORD_TITLE = "Слово в ЛС"
SESSION_SCORE_TITLE = "Угадали +1"
SESSION_SKIP_TITLE = "Пропустить"
SESSION_NEXT_TITLE = "Передать ход"
SESSION_FINISH_TITLE = "Завершить"

WORD_GAME_GET_TITLE = "Получить слово"
WORD_GAME_RESET_TITLE = "Новая игра (сбросить)"

DANGEROUS_WORDS_GAME_ID = "dangerous_words"

CB_BK_OPEN = "bk:open"
CB_BK_JOIN = "bk:join"
CB_BK_START = "bk:start"
CB_BK_CANCEL = "bk:cancel"
CB_BK_REVEAL = "bk:reveal"
CB_BK_VOTE_START = "bk:vote_start"
CB_BK_VOTE_TALLY = "bk:vote_tally"
CB_BK_NEXT = "bk:next"
CB_BK_VOTE_PREFIX = "bk:v:"
CB_BK_SOLO_START = "bk:solo_start"
CB_BK_SOLO_CANCEL = "bk:solo_cancel"
CB_BK_MODE = "bk:mode"
CB_BK_STORY_YES = "bk:story_yes"
CB_BK_STORY_NO = "bk:story_no"
CB_BK_STORY_TALLY = "bk:story_tally"

BUNKER_MODE_BASE_TITLE = "Режим: базовый"
BUNKER_MODE_STORY_TITLE = "Режим: история выживания"
BUNKER_STORY_YES_TITLE = "Справились"
BUNKER_STORY_NO_TITLE = "Не справились"

BUNKER_GAME_TITLE = "Бункер"
BUNKER_JOIN_TITLE = "Вступить в бункер"
BUNKER_START_TITLE = "Начать"
BUNKER_CANCEL_TITLE = "Отменить игру"
BUNKER_REVEAL_TITLE = "Открыть мою карту"
BUNKER_VOTE_START_TITLE = "Начать голосование"
BUNKER_VOTE_TALLY_TITLE = "Подвести итоги"
BUNKER_NEXT_TITLE = "Следующий раунд"

BUNKER_CARD_LABELS: dict[str, str] = {
    "superpower": "Суперсила",
    "phobia": "Фобия",
    "character": "Характер",
    "hobby": "Хобби",
    "baggage": "Багаж",
    "fact": "Факт",
    "special_condition": "Особое условие",
}
CB_ADMIN_STATS = "admin:stats"
CB_ADMIN_CLOSE = "admin:close"

CB_DG_OPEN = "dg:open"
CB_DG_EXPLAIN = "dg:explain"
CB_DG_WORD = "dg:word"
CB_DG_SEND = "dg:send"
CB_DG_NEXT = "dg:next"
CB_DG_CURSE = "dg:curse"
CB_DG_CURSE_KEEP = "dg:curse_keep"
CB_DG_CURSE_REROLL = "dg:curse_reroll"
CB_DG_BOSS = "dg:boss"
CB_DG_BOSS_KEEP = "dg:boss_keep"
CB_DG_BOSS_REROLL = "dg:boss_reroll"
CB_DG_FINISH = "dg:finish"

DG_EXPLAIN_TITLE = "Я объясняю"
DG_WORD_TITLE = "Тянуть слово"
DG_SEND_TITLE = "Отправить слово"
DG_NEXT_TITLE = "Передать ход"
DG_CURSE_TITLE = "Проклятие"
DG_BOSS_TITLE = "Босс (финал)"
DG_FINISH_TITLE = "Завершить"
DG_KEEP_TITLE = "Принять"
DG_REROLL_TITLE = "Реролл"

BOSSES_LIMIT = 200
CURSES_LIMIT = 200
TELEGRAM_MESSAGE_LIMIT = 3500
WORDS_LIMIT = 1600

BOSSES_HISTORY_KEY = "bosses"
CURSES_HISTORY_KEY = "curses"
WORDS_HISTORY_KEY = "words"

USER_BOSSES_TABLE_NAME = "user_bosses"
USER_CURSES_TABLE_NAME = "user_curses"
USER_WORDS_TABLE_NAME = "user_words"
