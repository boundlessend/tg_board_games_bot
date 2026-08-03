"""полная партия в бункер: лобби, раздача, раунды, голосование, финал"""

from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from constants import (
    CB_BK_CANCEL,
    CB_BK_JOIN,
    CB_BK_MODE,
    CB_BK_NEXT,
    CB_BK_OPEN,
    CB_BK_REVEAL,
    CB_BK_START,
    CB_BK_VOTE_PREFIX,
    CB_BK_VOTE_START,
    CB_BK_VOTE_TALLY,
)
from database import SQLiteHistoryStorage
from handlers.bunker import create_bunker_router, restore_bunker_sessions
from services.bunker import BunkerContent
from services.bunker_state import ROUNDS_TOTAL, BunkerSession, SoloLobby
from tests.fake_bot import BOT_USERNAME, CHAT_ADMIN_ID, RecordingSession, make_bot

GROUP_CHAT = -777_000
PLAYERS = (101, 102, 103, 104)
HOST = PLAYERS[0]

_counter = 0


def _next_id() -> int:
    """возрастающие идентификаторы апдейтов и сообщений"""
    global _counter
    _counter += 1
    return _counter


def _callback_message(user_id: int) -> Message:
    """сообщение-табло, на котором нажата кнопка"""
    return Message.model_construct(
        message_id=_next_id(),
        date=datetime(2026, 1, 1),
        chat=Chat.model_construct(id=GROUP_CHAT, type="supergroup"),
        from_user=User.model_construct(id=user_id, is_bot=False, first_name="И"),
        text="табло",
    )


async def _press(dispatcher: Dispatcher, bot: Bot, user_id: int, data: str) -> None:
    """нажатие кнопки участником партии"""
    callback = CallbackQuery.model_construct(
        id=f"cb{_next_id()}",
        from_user=User.model_construct(
            id=user_id, is_bot=False, first_name=f"Игрок{user_id}"
        ),
        chat_instance="ci",
        message=_callback_message(user_id),
        data=data,
    )
    await dispatcher.feed_update(
        bot, Update.model_construct(update_id=_next_id(), callback_query=callback)
    )


def _build(
    storage: SQLiteHistoryStorage, content: BunkerContent
) -> tuple[Dispatcher, Bot, RecordingSession, dict[int, BunkerSession]]:
    """собирает диспетчер с роутером бункера"""
    recording = RecordingSession()
    bot = make_bot(recording)
    sessions: dict[int, BunkerSession] = {}
    lobbies: dict[str, SoloLobby] = {}
    member_lobby: dict[int, str] = {}
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_bunker_router(
            content, storage, sessions, lobbies, member_lobby, BOT_USERNAME
        )
    )
    return dispatcher, bot, recording, sessions


async def _gather_players(dispatcher: Dispatcher, bot: Bot) -> None:
    """открывает лобби и набирает полный стол"""
    await _press(dispatcher, bot, HOST, CB_BK_OPEN)
    for player in PLAYERS:
        await _press(dispatcher, bot, player, CB_BK_JOIN)


async def test_start_requires_enough_players(
    storage: SQLiteHistoryStorage, bunker_content: BunkerContent
) -> None:
    """партия не стартует, пока игроков меньше минимума"""
    dispatcher, bot, recording, sessions = _build(storage, bunker_content)
    await _press(dispatcher, bot, HOST, CB_BK_OPEN)
    await _press(dispatcher, bot, HOST, CB_BK_JOIN)
    await _press(dispatcher, bot, HOST, CB_BK_START)

    assert sessions[GROUP_CHAT].phase == "lobby"
    assert any("игроков" in alert for alert in recording.alerts())


async def test_cards_are_dealt_privately_and_board_is_reused(
    storage: SQLiteHistoryStorage, bunker_content: BunkerContent
) -> None:
    """карты уходят в личку, а табло правится, а не плодится"""
    dispatcher, bot, recording, sessions = _build(storage, bunker_content)
    await _gather_players(dispatcher, bot)
    await _press(dispatcher, bot, HOST, CB_BK_START)

    session = sessions[GROUP_CHAT]
    assert session.phase == "reveal"
    assert session.hands_delivered == set(PLAYERS)
    for player in PLAYERS:
        assert any("Твой персонаж" in text for text in recording.sent_to(player))
    # катастрофа ушла в общий чат, карты - нет
    assert any("КАТАСТРОФА" in text for text in recording.sent_to(GROUP_CHAT))
    assert not any("Твой персонаж" in text for text in recording.sent_to(GROUP_CHAT))

    edits = [name for name in recording.method_names() if name == "EditMessageText"]
    assert edits, "табло должно правиться на месте"


async def test_blocked_player_stops_the_start(
    storage: SQLiteHistoryStorage, bunker_content: BunkerContent
) -> None:
    """если кому-то карта не дошла, партия не начинается"""
    dispatcher, bot, recording, sessions = _build(storage, bunker_content)
    await _gather_players(dispatcher, bot)
    recording.blocked_users.add(PLAYERS[2])

    await _press(dispatcher, bot, HOST, CB_BK_START)
    assert sessions[GROUP_CHAT].phase == "lobby"
    assert any("Не дошли карты" in alert for alert in recording.alerts())


async def test_reveal_and_vote_exclude_a_player(
    storage: SQLiteHistoryStorage, bunker_content: BunkerContent
) -> None:
    """раскрытие карт и голосование изгоняют кандидата"""
    dispatcher, bot, recording, sessions = _build(storage, bunker_content)
    await _gather_players(dispatcher, bot)
    await _press(dispatcher, bot, HOST, CB_BK_START)
    session = sessions[GROUP_CHAT]

    await _press(dispatcher, bot, HOST, CB_BK_REVEAL)
    assert session.revealed_count[HOST] == 1
    await _press(dispatcher, bot, HOST, CB_BK_REVEAL)
    assert session.revealed_count[HOST] == 1
    assert any("уже открыл" in alert for alert in recording.alerts())

    # на четверых голосование только в 4-м и 5-м раундах
    while session.votes_pending == 0 and session.round_no < ROUNDS_TOTAL:
        await _press(dispatcher, bot, HOST, CB_BK_NEXT)

    await _press(dispatcher, bot, HOST, CB_BK_VOTE_START)
    assert session.phase == "vote"

    victim = PLAYERS[3]
    for voter in PLAYERS[:3]:
        await _press(dispatcher, bot, voter, CB_BK_VOTE_PREFIX + str(victim))
    await _press(dispatcher, bot, HOST, CB_BK_VOTE_TALLY)

    assert victim in session.excluded
    assert any("Изгнан" in text for text in recording.sent_to(GROUP_CHAT))


async def test_outsider_cannot_vote(
    storage: SQLiteHistoryStorage, bunker_content: BunkerContent
) -> None:
    """посторонний не голосует за изгнание"""
    dispatcher, bot, recording, sessions = _build(storage, bunker_content)
    await _gather_players(dispatcher, bot)
    await _press(dispatcher, bot, HOST, CB_BK_START)
    session = sessions[GROUP_CHAT]

    while session.votes_pending == 0 and session.round_no < ROUNDS_TOTAL:
        await _press(dispatcher, bot, HOST, CB_BK_NEXT)
    await _press(dispatcher, bot, HOST, CB_BK_VOTE_START)

    await _press(dispatcher, bot, 555, CB_BK_VOTE_PREFIX + str(PLAYERS[1]))
    assert session.votes == {}
    assert any("активные игроки" in alert for alert in recording.alerts())


async def test_only_host_switches_mode(
    storage: SQLiteHistoryStorage, bunker_content: BunkerContent
) -> None:
    """режим партии меняет только создатель"""
    dispatcher, bot, recording, sessions = _build(storage, bunker_content)
    await _gather_players(dispatcher, bot)

    await _press(dispatcher, bot, PLAYERS[1], CB_BK_MODE)
    assert sessions[GROUP_CHAT].story_mode is False
    assert any("создатель" in alert for alert in recording.alerts())

    await _press(dispatcher, bot, HOST, CB_BK_MODE)
    assert sessions[GROUP_CHAT].story_mode is True


async def test_chat_admin_cancels_abandoned_game(
    storage: SQLiteHistoryStorage, bunker_content: BunkerContent
) -> None:
    """админ чата закрывает партию, брошенную создателем"""
    dispatcher, bot, recording, sessions = _build(storage, bunker_content)
    await _gather_players(dispatcher, bot)
    await _press(dispatcher, bot, HOST, CB_BK_START)

    await _press(dispatcher, bot, 555, CB_BK_CANCEL)
    assert GROUP_CHAT in sessions
    assert any("админ чата" in alert for alert in recording.alerts())

    await _press(dispatcher, bot, CHAT_ADMIN_ID, CB_BK_CANCEL)
    assert GROUP_CHAT not in sessions


async def test_started_game_survives_restart(
    storage: SQLiteHistoryStorage, bunker_content: BunkerContent
) -> None:
    """снапшот партии восстанавливает руки, фазу и раскрытые карты"""
    dispatcher, bot, _, sessions = _build(storage, bunker_content)
    await _gather_players(dispatcher, bot)
    await _press(dispatcher, bot, HOST, CB_BK_START)
    await _press(dispatcher, bot, HOST, CB_BK_REVEAL)

    restored: dict[int, BunkerSession] = {}
    lobbies: dict[str, SoloLobby] = {}
    member_lobby: dict[int, str] = {}
    await restore_bunker_sessions(storage, restored, lobbies, member_lobby)

    session = restored[GROUP_CHAT]
    assert session.phase == "reveal"
    assert session.revealed_count[HOST] == 1
    assert session.hands[HOST].superpower == sessions[GROUP_CHAT].hands[HOST].superpower
    assert session.reachable == set(PLAYERS)


async def test_cancelled_game_leaves_no_snapshot(
    storage: SQLiteHistoryStorage, bunker_content: BunkerContent
) -> None:
    """отменённая партия стирается из снапшотов"""
    dispatcher, bot, _, sessions = _build(storage, bunker_content)
    await _gather_players(dispatcher, bot)
    assert await storage.load_session_scope("bunker") != {}

    await _press(dispatcher, bot, HOST, CB_BK_CANCEL)
    assert await storage.load_session_scope("bunker") == {}
