"""личный чат: словесная игра, избранное, настройки, меню и инлайн"""

from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineQuery,
    Message,
    Update,
    User,
)

from constants import (
    CB_MAIN_MENU,
    CB_SETTINGS,
    CB_SETTINGS_TOGGLE_CYCLE,
    CB_WG_OPEN_PREFIX,
    CB_WG_RESET_PREFIX,
    CB_WG_WORD_PREFIX,
)
from database import SQLiteHistoryStorage
from handlers.favorites import create_favorites_router
from handlers.inline import create_inline_router
from handlers.settings import create_settings_router
from handlers.start import create_start_router
from handlers.word_games import create_word_games_router
from services.content import DangerousWordsContent, WordGame
from tests.fake_bot import RecordingSession, make_bot

USER = 77


def _message(text: str, chat_id: int = USER) -> Message:
    """личное сообщение пользователя"""
    return Message.model_construct(
        message_id=1,
        date=datetime(2026, 1, 1),
        chat=Chat.model_construct(id=chat_id, type="private"),
        from_user=User.model_construct(id=USER, is_bot=False, first_name="Ю"),
        text=text,
    )


def _callback(data: str) -> CallbackQuery:
    """нажатие кнопки в личном чате"""
    return CallbackQuery.model_construct(
        id="cb-1",
        from_user=User.model_construct(id=USER, is_bot=False, first_name="Ю"),
        chat_instance="ci",
        message=_message("меню"),
        data=data,
    )


async def _press(dispatcher: Dispatcher, bot: Bot, data: str) -> None:
    """прогоняет нажатие через диспетчер"""
    await dispatcher.feed_update(
        bot, Update.model_construct(update_id=1, callback_query=_callback(data))
    )


async def _send(dispatcher: Dispatcher, bot: Bot, text: str) -> None:
    """прогоняет команду через диспетчер"""
    await dispatcher.feed_update(
        bot, Update.model_construct(update_id=2, message=_message(text))
    )


async def test_word_game_issues_words_and_resets(
    storage: SQLiteHistoryStorage, word_games: list[WordGame]
) -> None:
    """игра выдаёт слово со счётчиком, сброс начинает круг заново"""
    recording = RecordingSession()
    bot = make_bot(recording)
    dispatcher = Dispatcher()
    dispatcher.include_router(create_word_games_router(word_games, storage))

    await _press(dispatcher, bot, CB_WG_OPEN_PREFIX + "whoami")
    await _press(dispatcher, bot, CB_WG_WORD_PREFIX + "whoami")
    await _press(dispatcher, bot, CB_WG_WORD_PREFIX + "whoami")

    texts = recording.sent_to(USER)
    assert "Слово (1/" in texts[-2]
    assert "Слово (2/" in texts[-1]
    assert await storage.count_user_game_words(USER, "whoami") == 2

    await _press(dispatcher, bot, CB_WG_RESET_PREFIX + "whoami")
    assert await storage.count_user_game_words(USER, "whoami") == 0


async def test_unknown_game_is_ignored(
    storage: SQLiteHistoryStorage, word_games: list[WordGame]
) -> None:
    """callback с чужим game_id не создаёт выдач"""
    recording = RecordingSession()
    bot = make_bot(recording)
    dispatcher = Dispatcher()
    dispatcher.include_router(create_word_games_router(word_games, storage))

    await _press(dispatcher, bot, CB_WG_WORD_PREFIX + "нет-такой-игры")
    assert recording.sent_to(USER) == []


async def test_auto_cycle_setting_survives_toggle(
    storage: SQLiteHistoryStorage,
) -> None:
    """переключатель авто-цикла сохраняется в базе"""
    recording = RecordingSession()
    bot = make_bot(recording)
    dispatcher = Dispatcher()
    dispatcher.include_router(create_settings_router(storage))

    await _press(dispatcher, bot, CB_SETTINGS)
    await _press(dispatcher, bot, CB_SETTINGS_TOGGLE_CYCLE)
    assert await storage.get_user_auto_cycle(USER) is False

    await _press(dispatcher, bot, CB_SETTINGS_TOGGLE_CYCLE)
    assert await storage.get_user_auto_cycle(USER) is True


async def test_favorites_flow(storage: SQLiteHistoryStorage) -> None:
    """избранное сохраняет последнее слово, показывает и очищает список"""
    recording = RecordingSession()
    bot = make_bot(recording)
    dispatcher = Dispatcher()
    dispatcher.include_router(create_favorites_router(storage))

    await _send(dispatcher, bot, "/fav")
    assert "Сначала получи слово" in recording.sent_to(USER)[-1]

    await storage.set_last_word(USER, "тестовое")
    await _send(dispatcher, bot, "/fav")
    assert "Добавлено в избранное" in recording.sent_to(USER)[-1]

    await _send(dispatcher, bot, "/fav")
    assert "Уже в избранном" in recording.sent_to(USER)[-1]

    await _send(dispatcher, bot, "/favorites")
    assert "тестовое" in recording.sent_to(USER)[-1]

    await _send(dispatcher, bot, "/favclear")
    assert await storage.get_favorites(USER) == []


async def test_private_menu_shows_only_private_games(
    word_games: list[WordGame],
) -> None:
    """в личке предлагаются только личные игры"""
    recording = RecordingSession()
    bot = make_bot(recording)
    dispatcher = Dispatcher()
    dispatcher.include_router(create_start_router(word_games))

    await _send(dispatcher, bot, "/start")
    markup = recording.calls[-1][1]["reply_markup"]["inline_keyboard"]
    labels = [button["text"] for row in markup for button in row]
    assert labels == ["Кто я?", "Настройки"]

    await _send(dispatcher, bot, "/help")
    assert "/forgetme" in recording.sent_to(USER)[-1]

    await _press(dispatcher, bot, CB_MAIN_MENU)
    assert recording.method_names()[-2] == "EditMessageText"


async def test_inline_query_returns_words(
    dangerous_content: DangerousWordsContent,
) -> None:
    """инлайн-режим отдаёт случайные слова без учёта истории"""
    recording = RecordingSession()
    bot = make_bot(recording)
    dispatcher = Dispatcher()
    dispatcher.include_router(create_inline_router(dangerous_content))

    query = InlineQuery.model_construct(
        id="iq-1",
        from_user=User.model_construct(id=USER, is_bot=False, first_name="Ю"),
        query="",
        offset="",
    )
    await dispatcher.feed_update(
        bot, Update.model_construct(update_id=3, inline_query=query)
    )

    name, payload = recording.calls[-1]
    assert name == "AnswerInlineQuery"
    assert len(payload["results"]) == 10
