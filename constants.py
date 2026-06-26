ADMIN_ACTIVITY_TITLE = "Активность"
ADMIN_CLOSE_TITLE = "Закрыть админку"
ADMIN_CSV_TITLE = "Выгрузить CSV"
ADMIN_STATS_TITLE = "Полный отчёт"
BACK_TO_DANGEROUS_WORDS_TITLE = "Назад к выбору роли"
BACK_TO_MAIN_MENU_TITLE = "Назад в главное меню"
CREATE_BOSS_TITLE = "Получить нового босса"
CREATE_CURSE_TITLE = "Получить новое проклятье"
CREATE_WORD_TITLE = "Получить новое слово"
DANGEROUS_WORDS_GAME_TITLE = "Опасные слова"
DANGEROUS_WORDS_HOST_TITLE = "Я ведущий"
DANGEROUS_WORDS_PLAYER_TITLE = "Я игрок"
NEW_GAME_TITLE = "Новая игра (сбросить всё)"
SETTINGS_TITLE = "Настройки"
RESET_BOSSES_TITLE = "Сбросить боссов"
RESET_CURSES_TITLE = "Сбросить проклятья"
RESET_WORDS_TITLE = "Сбросить слова"

CB_MAIN_MENU = "menu:main"
CB_DW_NEW_GAME = "dw:new_game"
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

TEAM_NAMES: tuple[str, ...] = ("Команда 1", "Команда 2")
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
BUNKER_GAME_ID = "bunker"
BUNKER_GAME_TITLE = "Бункер"

CB_BK_JOIN = "bk:join"
CB_BK_START = "bk:start"
CB_BK_CANCEL = "bk:cancel"
CB_BK_REVEAL = "bk:reveal"
CB_BK_VOTE_START = "bk:vote_start"
CB_BK_VOTE_TALLY = "bk:vote_tally"
CB_BK_NEXT = "bk:next"
CB_BK_VOTE_PREFIX = "bk:v:"

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
CB_DW_ROLES = "dw:roles"
CB_DW_HOST = "dw:host"
CB_DW_PLAYER = "dw:player"
CB_DW_WORD = "dw:word"
CB_DW_RESET_WORDS = "dw:reset_words"
CB_DW_CURSE = "dw:curse"
CB_DW_RESET_CURSES = "dw:reset_curses"
CB_DW_BOSS = "dw:boss"
CB_DW_RESET_BOSSES = "dw:reset_bosses"
CB_ADMIN_STATS = "admin:stats"
CB_ADMIN_CLOSE = "admin:close"

BOSSES_LIMIT = 100
CURSES_LIMIT = 100
TELEGRAM_MESSAGE_LIMIT = 3500
WORDS_LIMIT = 1000

BOSSES_HISTORY_KEY = "bosses"
CURSES_HISTORY_KEY = "curses"
WORDS_HISTORY_KEY = "words"

USER_BOSSES_TABLE_NAME = "user_bosses"
USER_CURSES_TABLE_NAME = "user_curses"
USER_WORDS_TABLE_NAME = "user_words"
