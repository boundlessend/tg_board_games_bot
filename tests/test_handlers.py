"""сценарии целиком через Dispatcher: права, фазы и доставка сообщений

это единственные тесты, которые проходят по настоящему пути обработки
события (фильтры, middleware, хендлер), поэтому именно они ловят регрессии
в правах и переходах фаз
"""

from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    Update,
    User,
)

from constants import (
    CB_BK_JOIN,
    CB_BK_OPEN,
    CB_DG_BOSS,
    CB_DG_BOSS_DROP,
    CB_DG_BOSS_KEEP,
    CB_DG_BOSS_REROLL,
    CB_DG_OPEN,
    CB_FORGET_ME_YES,
    CB_GS_CANCEL,
    CB_GS_FINISH,
    CB_GS_FINISH_NO,
    CB_GS_FINISH_YES,
    CB_GS_JOIN_PREFIX,
    CB_GS_NEW_PREFIX,
    CB_GS_START,
    CB_GS_WORD,
)
from database import SQLiteHistoryStorage
from handlers.bunker import create_bunker_router
from handlers.dangerous_group import DangerousGroup, create_dangerous_group_router
from handlers.group_session import GroupSession, create_group_session_router
from handlers.settings import create_settings_router
from services.bunker import BunkerContent
from services.bunker_state import BunkerSession, SoloLobby
from services.content import DangerousWordsContent, WordGame
from tests.fake_bot import (
    BOT_USERNAME,
    CHAT_ADMIN_ID,
    RecordingSession,
    make_bot,
)

GROUP_CHAT = -100_500
HOST = 1
PLAYER_TWO = 2
STRANGER = 3

_update_id = 0
_message_id = 0


def _next_update_id() -> int:
    """выдаёт возрастающий номер апдейта"""
    global _update_id
    _update_id += 1
    return _update_id


def _next_message_id() -> int:
    """выдаёт возрастающий номер сообщения"""
    global _message_id
    _message_id += 1
    return _message_id


def _user(user_id: int) -> User:
    """участник с предсказуемым именем"""
    return User.model_construct(id=user_id, is_bot=False, first_name=f"Игрок{user_id}")


def _message(chat_id: int, user_id: int, text: str) -> Message:
    """сообщение в чате нужного типа"""
    return Message.model_construct(
        message_id=_next_message_id(),
        date=datetime(2026, 1, 1),
        chat=Chat.model_construct(
            id=chat_id, type="private" if chat_id > 0 else "supergroup"
        ),
        from_user=_user(user_id),
        text=text,
    )


async def _press(
    dispatcher: Dispatcher,
    bot: Bot,
    chat_id: int,
    user_id: int,
    data: str,
) -> None:
    """эмулирует нажатие inline-кнопки в чате"""
    callback = CallbackQuery.model_construct(
        id=f"cb{_next_update_id()}",
        from_user=_user(user_id),
        chat_instance="chat-instance",
        message=_message(chat_id, user_id, "табло"),
        data=data,
    )
    await dispatcher.feed_update(
        bot,
        Update.model_construct(update_id=_next_update_id(), callback_query=callback),
    )


async def _send(
    dispatcher: Dispatcher, bot: Bot, chat_id: int, user_id: int, text: str
) -> None:
    """эмулирует отправку команды пользователем"""
    await dispatcher.feed_update(
        bot,
        Update.model_construct(
            update_id=_next_update_id(),
            message=_message(chat_id, user_id, text),
        ),
    )


@pytest.fixture
def session_pair() -> tuple[Dispatcher, RecordingSession]:
    """диспетчер без роутеров и его записывающая сессия"""
    recording = RecordingSession()
    return Dispatcher(), recording


async def _started_group_session(
    storage: SQLiteHistoryStorage, word_games: list[WordGame]
) -> tuple[Dispatcher, Bot, RecordingSession, dict[int, GroupSession]]:
    """собирает начатую партию «Крокодила» на две команды"""
    recording = RecordingSession()
    bot = make_bot(recording)
    sessions: dict[int, GroupSession] = {}
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_group_session_router(word_games, storage, sessions, BOT_USERNAME)
    )

    await _press(dispatcher, bot, GROUP_CHAT, HOST, CB_GS_NEW_PREFIX + "crocodile")
    await _press(dispatcher, bot, GROUP_CHAT, HOST, CB_GS_JOIN_PREFIX + "0")
    await _press(dispatcher, bot, GROUP_CHAT, PLAYER_TWO, CB_GS_JOIN_PREFIX + "1")
    await _press(dispatcher, bot, GROUP_CHAT, HOST, CB_GS_START)
    return dispatcher, bot, recording, sessions


async def test_group_session_lobby_offers_private_chat_link(
    storage: SQLiteHistoryStorage, word_games: list[WordGame]
) -> None:
    """в лобби есть ссылка на личку: слова приходят именно туда"""
    recording = RecordingSession()
    bot = make_bot(recording)
    sessions: dict[int, GroupSession] = {}
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_group_session_router(word_games, storage, sessions, BOT_USERNAME)
    )

    await _press(dispatcher, bot, GROUP_CHAT, HOST, CB_GS_NEW_PREFIX + "crocodile")
    edits = [payload for name, payload in recording.calls if name == "EditMessageText"]
    buttons = [
        button for row in edits[-1]["reply_markup"]["inline_keyboard"] for button in row
    ]
    assert any(BOT_USERNAME in str(button.get("url", "")) for button in buttons)


async def test_group_session_rejects_private_chat(
    storage: SQLiteHistoryStorage, word_games: list[WordGame]
) -> None:
    """командную игру нельзя открыть в личке"""
    recording = RecordingSession()
    bot = make_bot(recording)
    sessions: dict[int, GroupSession] = {}
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_group_session_router(word_games, storage, sessions, BOT_USERNAME)
    )

    await _press(dispatcher, bot, HOST, HOST, CB_GS_NEW_PREFIX + "crocodile")
    assert sessions == {}
    assert any("в беседе" in alert for alert in recording.alerts())


async def test_word_goes_only_to_the_explainer(
    storage: SQLiteHistoryStorage, word_games: list[WordGame]
) -> None:
    """слово уходит в личку объясняющему и не попадает в общий чат"""
    dispatcher, bot, recording, sessions = await _started_group_session(
        storage, word_games
    )
    await _press(dispatcher, bot, GROUP_CHAT, HOST, CB_GS_WORD)

    private = recording.sent_to(HOST)
    assert private and private[0].startswith("Слово:")
    assert not any("Слово:" in text for text in recording.sent_to(GROUP_CHAT))
    assert sessions[GROUP_CHAT].explainer_id == HOST


async def test_word_is_returned_to_pool_when_private_chat_closed(
    storage: SQLiteHistoryStorage, word_games: list[WordGame]
) -> None:
    """если личка закрыта, слово не считается выданным"""
    dispatcher, bot, recording, sessions = await _started_group_session(
        storage, word_games
    )
    recording.blocked_users.add(HOST)

    await _press(dispatcher, bot, GROUP_CHAT, HOST, CB_GS_WORD)
    assert sessions[GROUP_CHAT].issued == set()
    assert sessions[GROUP_CHAT].explainer_id is None
    assert any("ЛС" in alert for alert in recording.alerts())


async def test_wrong_team_cannot_take_word(
    storage: SQLiteHistoryStorage, word_games: list[WordGame]
) -> None:
    """слово берёт только игрок команды, чей ход"""
    dispatcher, bot, recording, sessions = await _started_group_session(
        storage, word_games
    )
    await _press(dispatcher, bot, GROUP_CHAT, PLAYER_TWO, CB_GS_WORD)

    assert sessions[GROUP_CHAT].explainer_id is None
    assert any("чей ход" in alert for alert in recording.alerts())


async def test_finish_asks_for_confirmation(
    storage: SQLiteHistoryStorage, word_games: list[WordGame]
) -> None:
    """«Завершить» доступно участникам, но требует подтверждения"""
    dispatcher, bot, recording, sessions = await _started_group_session(
        storage, word_games
    )

    await _press(dispatcher, bot, GROUP_CHAT, PLAYER_TWO, CB_GS_FINISH)
    assert GROUP_CHAT in sessions
    assert any("Завершить партию" in text for text in recording.sent_to(GROUP_CHAT))

    await _press(dispatcher, bot, GROUP_CHAT, PLAYER_TWO, CB_GS_FINISH_NO)
    assert GROUP_CHAT in sessions

    await _press(dispatcher, bot, GROUP_CHAT, PLAYER_TWO, CB_GS_FINISH_YES)
    assert GROUP_CHAT not in sessions


async def test_stranger_cannot_finish_session(
    storage: SQLiteHistoryStorage, word_games: list[WordGame]
) -> None:
    """посторонний в чате не может завершить партию"""
    dispatcher, bot, recording, sessions = await _started_group_session(
        storage, word_games
    )
    await _press(dispatcher, bot, GROUP_CHAT, STRANGER, CB_GS_FINISH_YES)
    assert GROUP_CHAT in sessions
    assert any("участники" in alert for alert in recording.alerts())


async def test_chat_admin_can_cancel_abandoned_session(
    storage: SQLiteHistoryStorage, word_games: list[WordGame]
) -> None:
    """админ чата снимает партию, брошенную создателем"""
    dispatcher, bot, _, sessions = await _started_group_session(storage, word_games)
    await _press(dispatcher, bot, GROUP_CHAT, CHAT_ADMIN_ID, CB_GS_CANCEL)
    assert GROUP_CHAT not in sessions


async def test_outsider_cannot_cancel_session(
    storage: SQLiteHistoryStorage, word_games: list[WordGame]
) -> None:
    """обычный участник чата не отменяет чужую партию"""
    dispatcher, bot, recording, sessions = await _started_group_session(
        storage, word_games
    )
    await _press(dispatcher, bot, GROUP_CHAT, STRANGER, CB_GS_CANCEL)
    assert GROUP_CHAT in sessions
    assert any("админ чата" in alert for alert in recording.alerts())


async def test_group_session_survives_restart(
    storage: SQLiteHistoryStorage, word_games: list[WordGame]
) -> None:
    """снапшот партии восстанавливается со счётом и составом команд"""
    from handlers.group_session import restore_group_sessions

    dispatcher, bot, _, sessions = await _started_group_session(storage, word_games)
    sessions[GROUP_CHAT].scores = [3, 1]
    await _press(dispatcher, bot, GROUP_CHAT, HOST, CB_GS_WORD)

    restored: dict[int, GroupSession] = {}
    await restore_group_sessions(storage, word_games, restored)
    assert restored[GROUP_CHAT].scores == [3, 1]
    assert restored[GROUP_CHAT].team_of == {HOST: 0, PLAYER_TWO: 1}
    # таймер не сериализуется: ход после рестарта доигрывают вручную
    assert restored[GROUP_CHAT].timer_task is None


async def _dangerous_game(
    storage: SQLiteHistoryStorage, content: DangerousWordsContent
) -> tuple[Dispatcher, Bot, RecordingSession, dict[int, DangerousGroup]]:
    """открывает партию «Опасных слов» в беседе"""
    recording = RecordingSession()
    bot = make_bot(recording)
    sessions: dict[int, DangerousGroup] = {}
    dispatcher = Dispatcher()
    dispatcher.include_router(create_dangerous_group_router(content, storage, sessions))
    await _press(dispatcher, bot, GROUP_CHAT, HOST, CB_DG_OPEN)
    return dispatcher, bot, recording, sessions


async def test_boss_reroll_returns_previous_to_pool(
    storage: SQLiteHistoryStorage, dangerous_content: DangerousWordsContent
) -> None:
    """реролл возвращает отвергнутого босса в колоду партии"""
    dispatcher, bot, _, sessions = await _dangerous_game(storage, dangerous_content)

    await _press(dispatcher, bot, GROUP_CHAT, HOST, CB_DG_BOSS)
    first = sessions[GROUP_CHAT].pending_boss_id
    assert sessions[GROUP_CHAT].issued_bosses == {first}

    await _press(dispatcher, bot, GROUP_CHAT, HOST, CB_DG_BOSS_REROLL)
    second = sessions[GROUP_CHAT].pending_boss_id
    assert second != first
    # отвергнутый босс снова доступен, занят только текущий
    assert sessions[GROUP_CHAT].issued_bosses == {second}


async def test_dropped_boss_unblocks_the_button(
    storage: SQLiteHistoryStorage, dangerous_content: DangerousWordsContent
) -> None:
    """снятое предложение босса не блокирует кнопку до конца партии"""
    dispatcher, bot, recording, sessions = await _dangerous_game(
        storage, dangerous_content
    )

    await _press(dispatcher, bot, GROUP_CHAT, HOST, CB_DG_BOSS)
    await _press(dispatcher, bot, GROUP_CHAT, HOST, CB_DG_BOSS_DROP)
    assert sessions[GROUP_CHAT].pending_boss_id is None
    assert sessions[GROUP_CHAT].issued_bosses == set()

    await _press(dispatcher, bot, GROUP_CHAT, HOST, CB_DG_BOSS)
    assert sessions[GROUP_CHAT].pending_boss_id is not None
    assert not any("уже на столе" in alert for alert in recording.alerts())


async def test_accepted_boss_updates_the_board(
    storage: SQLiteHistoryStorage, dangerous_content: DangerousWordsContent
) -> None:
    """после «Принять» табло сразу показывает раскрытого босса"""
    dispatcher, bot, recording, sessions = await _dangerous_game(
        storage, dangerous_content
    )
    await _press(dispatcher, bot, GROUP_CHAT, HOST, CB_DG_BOSS)
    await _press(dispatcher, bot, GROUP_CHAT, HOST, CB_DG_BOSS_KEEP)

    assert sessions[GROUP_CHAT].boss_revealed is True
    assert any("Босс: раскрыт" in text for text in recording.sent_to(GROUP_CHAT))


async def test_only_host_or_admin_draws_boss(
    storage: SQLiteHistoryStorage, dangerous_content: DangerousWordsContent
) -> None:
    """посторонний не тянет босса, админ чата может"""
    dispatcher, bot, recording, sessions = await _dangerous_game(
        storage, dangerous_content
    )

    await _press(dispatcher, bot, GROUP_CHAT, STRANGER, CB_DG_BOSS)
    assert sessions[GROUP_CHAT].pending_boss_id is None
    assert any("админ чата" in alert for alert in recording.alerts())

    await _press(dispatcher, bot, GROUP_CHAT, CHAT_ADMIN_ID, CB_DG_BOSS)
    assert sessions[GROUP_CHAT].pending_boss_id is not None


async def test_bunker_lobby_marks_unreachable_players(
    storage: SQLiteHistoryStorage, bunker_content: BunkerContent
) -> None:
    """лобби показывает, кому карты не дойдут, ещё до старта"""
    recording = RecordingSession()
    bot = make_bot(recording)
    sessions: dict[int, BunkerSession] = {}
    lobbies: dict[str, SoloLobby] = {}
    member_lobby: dict[int, str] = {}
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_bunker_router(
            bunker_content, storage, sessions, lobbies, member_lobby, BOT_USERNAME
        )
    )

    await _press(dispatcher, bot, GROUP_CHAT, HOST, CB_BK_OPEN)
    recording.blocked_users.add(PLAYER_TWO)
    await _press(dispatcher, bot, GROUP_CHAT, HOST, CB_BK_JOIN)
    await _press(dispatcher, bot, GROUP_CHAT, PLAYER_TWO, CB_BK_JOIN)

    session = sessions[GROUP_CHAT]
    assert session.reachable == {HOST}
    assert any("не открыли ЛС" in text for text in recording.sent_to(GROUP_CHAT))


async def test_forget_me_wipes_user_data(storage: SQLiteHistoryStorage) -> None:
    """подтверждённый /forgetme стирает данные пользователя"""
    recording = RecordingSession()
    bot = make_bot(recording)
    dispatcher = Dispatcher()
    dispatcher.include_router(create_settings_router(storage))

    await storage.save_user_word(HOST, "слово")
    await storage.add_favorite(HOST, "любимое")

    await _send(dispatcher, bot, HOST, HOST, "/forgetme")
    assert any("Удалить все твои данные" in text for text in recording.sent_to(HOST))

    await _press(dispatcher, bot, HOST, HOST, CB_FORGET_ME_YES)
    assert await storage.count_user_words(HOST) == 0
    assert await storage.get_favorites(HOST) == []
