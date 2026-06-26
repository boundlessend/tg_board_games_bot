"""smoke-проверка сборки и ключевой логики бота без сети и токена

запуск: pytest (из корня репозитория)
"""

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot, Dispatcher  # noqa: E402

import keyboards  # noqa: E402
from config import _parse_admin_ids, _resolve_database_path  # noqa: E402
from database import SQLiteHistoryStorage, iso_days_ago  # noqa: E402
from handlers.admin import (  # noqa: E402
    _build_statistics_csv,
    _build_summary,
    create_admin_router,
)
from handlers.bunker import (  # noqa: E402
    BunkerSession,
    Challenge,
    SoloLobby,
    _alive,
    _apply_casualty,
    _begin_round,
    _board_keyboard,
    _build_finale_queue,
    _drop_lobby,
    _exclude_player,
    _generate_code,
    _lookup_lobby,
    _open_vote,
    _render_board,
    _render_finale,
    _render_solo_intro,
    _render_solo_lobby,
    _render_story_verdict,
    _story_survivors,
    create_bunker_router,
)
from handlers.content_admin import (  # noqa: E402
    _addword_usage,
    _is_sqlite_file,
    _parse_custom_id,
    _parse_pair,
    _parse_words_pack,
    create_content_admin_router,
)
from handlers.dangerous_group import (  # noqa: E402
    DangerousGroup,
    _pick as _dg_pick,
    _pick_boss as _dg_pick_boss,
    _pick_curse as _dg_pick_curse,
    _render_board as _render_dg_board,
    create_dangerous_group_router,
)
from handlers.favorites import create_favorites_router  # noqa: E402
from handlers.group_session import (  # noqa: E402
    GroupSession,
    _cancel_timer,
    _is_group,
    _parse_count,
    _parse_seconds,
    _parse_team,
    _pick_word,
    _render_lobby,
    _render_play,
    _render_scores,
    _run_timer,
    create_group_session_router,
)
from handlers.inline import create_inline_router  # noqa: E402
from handlers.settings import create_settings_router  # noqa: E402
from handlers.start import create_start_router  # noqa: E402
from handlers.word_games import (  # noqa: E402
    _data_prefix,
    _resolve_game,
    _select_word_once,
    _select_word_with_cycle,
    create_word_games_router,
)
from services.bunker import (  # noqa: E402
    MAX_PLAYERS,
    deal_hands,
    load_bunker_content,
    pick_pairs,
    rounds_plan,
    vote_leaders,
)
from services.random_generator import (  # noqa: E402
    EmptyPoolError,
    load_dangerous_words_content,
    load_word_games,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def test_config_resolves_database_path() -> None:
    """DATABASE_PATH из окружения переопределяет путь к базе"""
    project = Path("/proj")
    assert _resolve_database_path(None, project) == project / "bot.sqlite3"
    assert _resolve_database_path("  ", project) == project / "bot.sqlite3"
    assert _resolve_database_path("/db/bot.sqlite3", project) == Path(
        "/db/bot.sqlite3"
    )


def test_content_loads_and_validates() -> None:
    """контент опасных слов и словесных игр загружается без дублей"""
    content = load_dangerous_words_content(DATA_DIR)
    assert len(content.words) == 1600
    assert len(content.curses) == 200
    assert len(content.bosses) == 200

    games = load_word_games(DATA_DIR)
    assert [game.game_id for game in games] == [
        "crocodile",
        "alias",
        "whoami",
    ]
    for game in games:
        assert len(game.words) >= 180
        lowered = [word.lower() for word in game.words]
        assert len(lowered) == len(set(lowered))

    whoami = next(game for game in games if game.game_id == "whoami")
    assert "Гарри Поттер" in whoami.words


def test_pure_helpers() -> None:
    """чистые помощники парсинга и резолва работают корректно"""
    assert _parse_pair("назв | опис") == ("назв", "опис")
    assert _parse_pair("без разделителя") is None
    assert "crocodile" in _addword_usage({"dangerous_words", "crocodile"})

    assert _parse_custom_id("cc_3", "cc_") == 3
    assert _parse_custom_id("3", "cc_") == 3
    assert _parse_custom_id("abc", "cc_") is None
    assert _parse_words_pack(b'["a", "b"]') == ["a", "b"]
    assert _parse_words_pack(b"a\nb, c") == ["a", "b", "c"]

    games = load_word_games(DATA_DIR)
    games_by_id = {game.game_id: game for game in games}
    resolved = _resolve_game("wg:open:alias", "wg:open:", games_by_id)
    assert resolved is not None and resolved.game_id == "alias"
    assert _resolve_game("wg:open:zzz", "wg:open:", games_by_id) is None

    is_word = _data_prefix("wg:word:")

    class FakeCallback:
        data = "wg:word:crocodile"

    assert is_word(FakeCallback()) is True


async def _exercise_storage() -> None:
    """проверяет хранилище, no-repeat, авто-цикл и пользовательский контент"""
    db_path = Path(tempfile.mkdtemp()) / "db" / "smoke.sqlite3"
    assert not db_path.parent.exists()
    storage = SQLiteHistoryStorage(db_path)
    await storage.initialize()
    assert db_path.parent.exists()

    content = load_dangerous_words_content(DATA_DIR)
    games = load_word_games(DATA_DIR)
    crocodile = next(g for g in games if g.game_id == "crocodile")

    word_one, cycle_one = await _select_word_with_cycle(
        crocodile.words, storage, 5, "crocodile"
    )
    word_two, cycle_two = await _select_word_with_cycle(
        crocodile.words, storage, 5, "crocodile"
    )
    assert word_one != word_two
    assert cycle_one is False and cycle_two is False
    assert await storage.count_user_game_words(5, "crocodile") == 2

    pool = ["a", "b"]
    await _select_word_with_cycle(pool, storage, 9, "tg")
    await _select_word_with_cycle(pool, storage, 9, "tg")
    _, is_new_cycle = await _select_word_with_cycle(pool, storage, 9, "tg")
    assert is_new_cycle is True

    await _select_word_once(["один"], storage, 11, "once")
    raised = False
    try:
        await _select_word_once(["один"], storage, 11, "once")
    except EmptyPoolError:
        raised = True
    assert raised

    assert await storage.get_user_auto_cycle(11) is True
    await storage.set_user_auto_cycle(11, False)
    assert await storage.get_user_auto_cycle(11) is False
    await storage.set_user_auto_cycle(11, True)
    assert await storage.get_user_auto_cycle(11) is True

    await storage.set_last_word(3, "избранное_слово")
    assert await storage.get_last_word(3) == "избранное_слово"
    assert await storage.get_last_word(999) is None
    assert await storage.add_favorite(3, "избранное_слово") is True
    assert await storage.add_favorite(3, "избранное_слово") is False
    assert await storage.get_favorites(3) == ["избранное_слово"]
    await storage.clear_favorites(3)
    assert await storage.get_favorites(3) == []

    await storage.save_user_word(7, "слово")
    await storage.reset_user_all(7)
    assert await storage.count_user_words(7) == 0

    await storage.add_custom_word("crocodile", "кастом")
    assert "кастом" in await storage.get_custom_words("crocodile")
    duplicate_raised = False
    try:
        await storage.add_custom_word("crocodile", "кастом")
    except Exception as error:
        duplicate_raised = error.__class__.__name__ == "DuplicateHistoryItemError"
    assert duplicate_raised
    await storage.add_custom_curse("заголовок", "описание")
    custom_curses = await storage.get_custom_curses()
    assert custom_curses and custom_curses[0].id.startswith("cc_")
    await storage.add_custom_boss("имя", "описание")
    custom_bosses = await storage.get_custom_bosses()
    assert custom_bosses and custom_bosses[0].id.startswith("cb_")

    assert await storage.delete_custom_word("crocodile", "кастом") is True
    assert await storage.delete_custom_word("crocodile", "кастом") is False
    curse_id = int(custom_curses[0].id.removeprefix("cc_"))
    assert await storage.delete_custom_curse(curse_id) is True
    assert await storage.delete_custom_curse(curse_id) is False

    await storage.save_user_word(1, "альфа")
    await storage.save_user_word(2, "альфа")
    statistics = await storage.get_all_user_statistics()
    assert "альфа x2" in _build_summary(statistics, 0, 0)
    csv_text = _build_statistics_csv(statistics)
    assert csv_text.splitlines()[0] == "telegram_id,words,curses,bosses"

    assert await storage.count_issuances_since(iso_days_ago(1)) > 0
    assert await storage.count_active_users_since(iso_days_ago(1)) > 0
    by_day = await storage.issuances_by_day(iso_days_ago(1))
    assert by_day and by_day[-1][1] > 0
    assert await storage.count_issuances_since("9999-01-01T00:00:00+00:00") == 0

    dispatcher = Dispatcher()
    dispatcher.include_router(create_start_router(games))
    dispatcher.include_router(create_settings_router(storage))
    dispatcher.include_router(create_favorites_router(storage))
    dispatcher.include_router(
        create_admin_router(content, storage, frozenset({1}), games)
    )
    dispatcher.include_router(
        create_content_admin_router(storage, frozenset({1}), games)
    )
    dispatcher.include_router(create_inline_router(content))
    dispatcher.include_router(create_word_games_router(games, storage))
    dispatcher.include_router(create_group_session_router(games, storage))
    dispatcher.include_router(create_bunker_router(load_bunker_content(DATA_DIR)))
    dispatcher.include_router(create_dangerous_group_router(content, storage))

    private_menu = keyboards.create_private_menu_keyboard(games)
    private_labels = [
        button.text
        for row in private_menu.inline_keyboard
        for button in row
    ]
    assert private_labels == ["Кто я?", "Настройки"]

    group_menu = keyboards.create_group_menu_keyboard(games)
    group_labels = [
        button.text
        for row in group_menu.inline_keyboard
        for button in row
    ]
    assert group_labels == [
        "Крокодил",
        "Алиас",
        "Опасные слова",
        "Бункер",
    ]
    for keyboard in [
        private_menu,
        group_menu,
        keyboards.create_word_game_keyboard("crocodile"),
    ]:
        for row in keyboard.inline_keyboard:
            for button in row:
                assert button.callback_data is not None
                assert len(button.callback_data.encode()) <= 64


def test_storage_and_wiring() -> None:
    """прогоняет асинхронные проверки хранилища и связки роутеров"""
    asyncio.run(_exercise_storage())


def test_parse_admin_ids() -> None:
    """ADMIN_IDS парсится в множество telegram id"""
    assert _parse_admin_ids(None) == frozenset()
    assert _parse_admin_ids("") == frozenset()
    assert _parse_admin_ids("111, 222 ,333") == frozenset({111, 222, 333})


async def _exercise_backup_restore() -> None:
    """проверяет замену базы и распознавание файла SQLite"""
    base = Path(tempfile.mkdtemp())
    path_a = base / "a.sqlite3"
    path_b = base / "b.sqlite3"

    storage_a = SQLiteHistoryStorage(path_a)
    await storage_a.initialize()
    await storage_a.save_user_word(1, "из_a")

    storage_b = SQLiteHistoryStorage(path_b)
    await storage_b.initialize()
    await storage_b.save_user_word(2, "из_b")

    assert _is_sqlite_file(path_b) is True
    not_sqlite = base / "x.bin"
    not_sqlite.write_bytes(b"not a database")
    assert _is_sqlite_file(not_sqlite) is False

    await storage_a.replace_database(path_b)
    assert await storage_a.count_user_words(2) == 1
    assert await storage_a.count_user_words(1) == 0


def test_backup_restore() -> None:
    """прогоняет проверки бэкапа/восстановления"""
    asyncio.run(_exercise_backup_restore())


def test_group_session_helpers() -> None:
    """проверяет чистую логику групповой сессии"""
    games = load_word_games(DATA_DIR)
    session = GroupSession(
        game=games[0],
        host_id=1,
        team_count=2,
        turn_seconds=60,
        current_team=0,
        started=True,
        explainer_id=None,
    )
    session.players = {1: "Аня", 2: "Боря"}
    session.team_of = {1: 0, 2: 1}
    session.scores = [0, 0]

    first, reset_first = _pick_word(["a", "b"], session.issued)
    second, reset_second = _pick_word(["a", "b"], session.issued)
    assert {first, second} == {"a", "b"}
    assert reset_first is False and reset_second is False
    _, reset_third = _pick_word(["a", "b"], session.issued)
    assert reset_third is True

    session.scores[0] = 2
    assert "Команда 1: 2" in _render_play(session)
    assert "Победитель: Команда 1" in _render_scores(session)

    assert _parse_team("1", session.team_count) == 1
    assert _parse_team("9", session.team_count) is None
    assert _parse_team("x", session.team_count) is None
    assert _parse_count("3") == 3
    assert _parse_count("1") is None
    assert _parse_count("5") is None
    assert _parse_seconds("60") == 60
    assert _parse_seconds("45") is None
    assert _parse_seconds("x") is None
    _cancel_timer(session)

    three = GroupSession(
        game=games[0],
        host_id=1,
        team_count=3,
        turn_seconds=60,
        current_team=0,
        started=False,
        explainer_id=None,
    )
    assert "Команд: 3" in _render_lobby(three)
    assert "Команда 3" in _render_lobby(three)

    assert _is_group("supergroup") is True
    assert _is_group("private") is False


class _RecordingBot:
    """заглушка бота: запоминает отправленные сообщения"""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(
        self, chat_id: int, text: str, reply_markup: object = None
    ) -> None:
        self.sent.append((chat_id, text))


async def _exercise_turn_timer() -> None:
    """по истечении таймера ход уходит следующей команде с уведомлением"""
    games = load_word_games(DATA_DIR)
    session = GroupSession(
        game=games[0],
        host_id=1,
        team_count=2,
        turn_seconds=0,
        current_team=0,
        started=True,
        explainer_id=7,
    )
    session.scores = [0, 0]
    bot = _RecordingBot()
    await _run_timer(123, session, cast(Bot, bot))
    assert session.current_team == 1
    assert session.explainer_id is None
    assert session.timer_task is None
    assert bot.sent and bot.sent[0][0] == 123
    assert "Время вышло" in bot.sent[0][1]


def test_turn_timer() -> None:
    """проверяет авто-завершение хода по таймеру"""
    asyncio.run(_exercise_turn_timer())


def test_dangerous_group_helpers() -> None:
    """проверяет чистую логику командных «опасных слов»"""
    content = load_dangerous_words_content(DATA_DIR)
    session = DangerousGroup(
        host_id=1,
        explaining_team=0,
        explainer_id=None,
        explainer_name=None,
    )
    board = _render_dg_board(session)
    assert "Загадывает: Команда 2" in board
    assert "Объясняет: Команда 1" in board
    assert "Объясняющий: не выбран" in board
    assert "в колоде" in board

    session.explaining_team = 1
    session.explainer_name = "Аня"
    session.boss_revealed = True
    board2 = _render_dg_board(session)
    assert "Загадывает: Команда 1" in board2
    assert "Объясняющий: Аня" in board2
    assert "Босс: раскрыт" in board2

    issued: set[str] = set()
    first = _dg_pick(["a", "b"], issued)
    second = _dg_pick(["a", "b"], issued)
    assert {first, second} == {"a", "b"}
    assert _dg_pick(["a", "b"], issued) in {"a", "b"}

    curse = _dg_pick_curse(content.curses, session.issued_curses)
    assert curse is not None and curse.id in session.issued_curses
    assert _dg_pick_curse([], set()) is None

    boss = _dg_pick_boss(content.bosses, session.issued_bosses)
    assert boss is not None and boss.id in session.issued_bosses
    assert _dg_pick_boss([], set()) is None


def test_bunker_content_and_logic() -> None:
    """контент и чистая логика бункера работают корректно"""
    content = load_bunker_content(DATA_DIR)

    hands = deal_hands(content, MAX_PLAYERS)
    assert len(hands) == MAX_PLAYERS
    superpowers = [hand.superpower for hand in hands]
    assert len(superpowers) == len(set(superpowers))
    assert hands[0].special_condition in content.special_conditions

    pairs = pick_pairs(content, 5)
    assert len(pairs) == 5
    assert pairs[0][0] in content.bunker_items
    assert pairs[0][1] in content.threats

    plan = rounds_plan(7)
    assert plan.votes_per_round == (0, 1, 1, 1, 1)
    assert plan.exclusions == 4
    assert plan.seats == 3

    leaders, top = vote_leaders({1: 5, 2: 5, 3: 8})
    assert leaders == [5] and top == 2
    tied, tied_top = vote_leaders({1: 5, 2: 8})
    assert set(tied) == {5, 8} and tied_top == 1
    assert vote_leaders({}) == ([], 0)


def test_bunker_handler_helpers() -> None:
    """движок партии бункер: рендер табло, фазы, изгнание, финал"""
    content = load_bunker_content(DATA_DIR)
    session = BunkerSession(host_id=1, board_chat_id=-100)
    session.players = {1: "Аня", 2: "Боря", 3: "Витя", 4: "Гена"}
    assert "Сбор" in _render_board(session)

    session.catastrophe = "тест"
    session.pairs = pick_pairs(content, 5)
    session.plan = rounds_plan(4)
    session.hands = dict(zip(session.players, deal_hands(content, 4), strict=True))
    session.revealed_count = {pid: 0 for pid in session.players}
    _begin_round(session, 4)
    board = _render_board(session)
    assert "раунд 4/5" in board and "Аня" in board
    assert session.votes_pending == 1

    _open_vote(session, _alive(session))
    assert session.phase == "vote"
    keyboard = _board_keyboard(session)
    texts = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "Боря" in texts and "Подвести итоги" in texts
    for row in keyboard.inline_keyboard:
        for button in row:
            assert button.callback_data is not None
            assert len(button.callback_data.encode()) <= 64

    _exclude_player(session, 2)
    assert 2 in session.excluded and session.revealed_count[2] == 5
    assert _alive(session) == [1, 3, 4]
    assert "ФИНАЛ" in _render_finale(session)


def test_bunker_solo_lobby_helpers() -> None:
    """режим «отдельно»: код, реестры и рендер общего стола"""
    content = load_bunker_content(DATA_DIR)
    code = _generate_code({"1234", "5678"})
    assert code.isdigit() and code not in {"1234", "5678"}

    lobbies: dict[str, SoloLobby] = {}
    member_lobby: dict[int, str] = {}
    lobby = SoloLobby(host_id=1, code=code)
    lobby.members = {1: "Аня", 2: "Боря", 3: "Витя", 4: "Гена"}
    lobbies[code] = lobby
    for member_id in lobby.members:
        member_lobby[member_id] = code

    assert _lookup_lobby(2, lobbies, member_lobby) is lobby
    assert _lookup_lobby(99, lobbies, member_lobby) is None
    assert code in _render_solo_lobby(lobby) and "Аня" in _render_solo_lobby(lobby)

    intro = _render_solo_intro(
        "катастрофа", pick_pairs(content, 5), rounds_plan(4), 4
    )
    assert "катастрофа" in intro and "Мест в бункере" in intro

    _drop_lobby(lobby, lobbies, member_lobby)
    assert lobbies == {} and member_lobby == {}


def test_bunker_story_finale() -> None:
    """финал «история выживания»: очередь испытаний, потери, итог"""
    content = load_bunker_content(DATA_DIR)
    session = BunkerSession(host_id=1, board_chat_id=-100, story_mode=True)
    session.players = {1: "Аня", 2: "Боря", 3: "Витя", 4: "Гена"}
    session.catastrophe = "падение неба"
    session.pairs = pick_pairs(content, 5)
    session.survivors_bunker = [1, 2]
    session.survivors_exiles = [3, 4]

    queue = _build_finale_queue(session, content)
    groups = [challenge.group for challenge in queue]
    assert groups == ["bunker", "exiles", "exiles", "all"]
    assert queue[-1].kind == "catastrophe"

    threat = Challenge(group="bunker", kind="threat", text="прорыв воды")
    text = _apply_casualty(session, threat)
    assert "погиб" in text.lower()
    assert len(session.survivors_bunker) <= 2

    catastrophe = Challenge(group="all", kind="catastrophe", text="падение неба")
    _apply_casualty(session, catastrophe)
    assert _story_survivors(session) == []
    assert "Не выжил никто" in _render_story_verdict(session)
