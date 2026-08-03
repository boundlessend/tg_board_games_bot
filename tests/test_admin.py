"""админка: доступ по id, сводка, отчёты файлом и управление контентом"""

from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.types import (
    CallbackQuery,
    Chat,
    Document,
    Message,
    Update,
    User,
)

from constants import CB_ADMIN_ACTIVITY, CB_ADMIN_CSV, CB_ADMIN_STATS
from database import SQLiteHistoryStorage
from handlers.admin import create_admin_router
from handlers.content_admin import (
    _parse_pair,
    _parse_words_pack,
    create_content_admin_router,
)
from services.content import DangerousWordsContent, WordGame
from tests.fake_bot import RecordingSession, make_bot

ADMIN = 11
OUTSIDER = 12


def _message(user_id: int, text: str, chat_type: str = "private") -> Message:
    """сообщение от указанного пользователя"""
    return Message.model_construct(
        message_id=1,
        date=datetime(2026, 1, 1),
        chat=Chat.model_construct(
            id=user_id if chat_type == "private" else -500, type=chat_type
        ),
        from_user=User.model_construct(id=user_id, is_bot=False, first_name="A"),
        text=text,
    )


async def _send(
    dispatcher: Dispatcher,
    bot: Bot,
    user_id: int,
    text: str,
    chat_type: str = "private",
) -> None:
    """прогоняет команду через диспетчер"""
    await dispatcher.feed_update(
        bot,
        Update.model_construct(update_id=1, message=_message(user_id, text, chat_type)),
    )


async def _press(dispatcher: Dispatcher, bot: Bot, user_id: int, data: str) -> None:
    """прогоняет нажатие кнопки админ-меню"""
    callback = CallbackQuery.model_construct(
        id="cb",
        from_user=User.model_construct(id=user_id, is_bot=False, first_name="A"),
        chat_instance="ci",
        message=_message(user_id, "админка"),
        data=data,
    )
    await dispatcher.feed_update(
        bot, Update.model_construct(update_id=2, callback_query=callback)
    )


def _admin_dispatcher(
    storage: SQLiteHistoryStorage,
    content: DangerousWordsContent,
    word_games: list[WordGame],
) -> Dispatcher:
    """диспетчер с админскими роутерами"""
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_admin_router(content, storage, frozenset({ADMIN}), word_games)
    )
    dispatcher.include_router(
        create_content_admin_router(storage, frozenset({ADMIN}), word_games)
    )
    return dispatcher


async def test_admin_menu_is_closed_for_outsiders(
    storage: SQLiteHistoryStorage,
    dangerous_content: DangerousWordsContent,
    word_games: list[WordGame],
) -> None:
    """не-админ не получает ответа на /admin"""
    recording = RecordingSession()
    bot = make_bot(recording)
    dispatcher = _admin_dispatcher(storage, dangerous_content, word_games)

    await _send(dispatcher, bot, OUTSIDER, "/admin")
    assert recording.calls == []


async def test_admin_menu_is_closed_in_group(
    storage: SQLiteHistoryStorage,
    dangerous_content: DangerousWordsContent,
    word_games: list[WordGame],
) -> None:
    """админка работает только в личке, даже для админа"""
    recording = RecordingSession()
    bot = make_bot(recording)
    dispatcher = _admin_dispatcher(storage, dangerous_content, word_games)

    await _send(dispatcher, bot, ADMIN, "/admin", chat_type="supergroup")
    assert recording.calls == []


async def test_summary_counts_every_kind_of_issue(
    storage: SQLiteHistoryStorage,
    dangerous_content: DangerousWordsContent,
    word_games: list[WordGame],
) -> None:
    """сводка разложена по видам, чтобы числа сходились между собой"""
    recording = RecordingSession()
    bot = make_bot(recording)
    dispatcher = _admin_dispatcher(storage, dangerous_content, word_games)

    await storage.save_user_word(1, "альфа")
    await storage.save_user_word(2, "альфа")
    await storage.save_user_game_word(1, "alias", "бета")

    await _send(dispatcher, bot, ADMIN, "/admin")
    summary = recording.sent_to(ADMIN)[-1]
    assert "Пользователей: 2" in summary
    assert "Слов «Опасные слова»: 2" in summary
    assert "Слов словесных игр: 1" in summary
    assert "Всего выдач: 3" in summary
    assert "альфа x2" in summary


async def test_full_report_is_sent_as_a_file(
    storage: SQLiteHistoryStorage,
    dangerous_content: DangerousWordsContent,
    word_games: list[WordGame],
) -> None:
    """подробный отчёт уходит документом, а не серией сообщений"""
    recording = RecordingSession()
    bot = make_bot(recording)
    dispatcher = _admin_dispatcher(storage, dangerous_content, word_games)
    await storage.save_user_word(1, "альфа")

    await _press(dispatcher, bot, ADMIN, CB_ADMIN_STATS)
    assert "SendDocument" in recording.method_names()
    assert recording.sent_to(ADMIN) == []


async def test_csv_and_activity_reports(
    storage: SQLiteHistoryStorage,
    dangerous_content: DangerousWordsContent,
    word_games: list[WordGame],
) -> None:
    """csv отдаётся файлом, активность - текстом"""
    recording = RecordingSession()
    bot = make_bot(recording)
    dispatcher = _admin_dispatcher(storage, dangerous_content, word_games)
    await storage.save_user_word(1, "альфа")

    await _press(dispatcher, bot, ADMIN, CB_ADMIN_CSV)
    assert "SendDocument" in recording.method_names()

    await _press(dispatcher, bot, ADMIN, CB_ADMIN_ACTIVITY)
    assert "Выдачи по дням" in recording.sent_to(ADMIN)[-1]


async def test_content_commands_add_and_remove(
    storage: SQLiteHistoryStorage,
    dangerous_content: DangerousWordsContent,
    word_games: list[WordGame],
) -> None:
    """админ добавляет и удаляет пользовательский контент"""
    recording = RecordingSession()
    bot = make_bot(recording)
    dispatcher = _admin_dispatcher(storage, dangerous_content, word_games)

    await _send(dispatcher, bot, ADMIN, "/addword alias новое")
    assert "новое" in await storage.get_custom_words("alias")

    await _send(dispatcher, bot, ADMIN, "/addword alias новое")
    assert "уже есть" in recording.sent_to(ADMIN)[-1]

    await _send(dispatcher, bot, ADMIN, "/addword нетигры слово")
    assert "Формат" in recording.sent_to(ADMIN)[-1]

    await _send(dispatcher, bot, ADMIN, "/addcurse Название | Описание")
    assert (await storage.get_custom_curses())[0].title == "Название"

    await _send(dispatcher, bot, ADMIN, "/addboss Имя | Описание")
    assert (await storage.get_custom_bosses())[0].name == "Имя"

    await _send(dispatcher, bot, ADMIN, "/listcontent")
    assert "новое" in recording.sent_to(ADMIN)[-1]

    await _send(dispatcher, bot, ADMIN, "/delword alias новое")
    assert await storage.get_custom_words("alias") == []


async def test_oversized_import_is_rejected(
    storage: SQLiteHistoryStorage,
    dangerous_content: DangerousWordsContent,
    word_games: list[WordGame],
) -> None:
    """слишком большой пак отклоняется до скачивания"""
    recording = RecordingSession()
    bot = make_bot(recording)
    dispatcher = _admin_dispatcher(storage, dangerous_content, word_games)

    message = Message.model_construct(
        message_id=1,
        date=datetime(2026, 1, 1),
        chat=Chat.model_construct(id=ADMIN, type="private"),
        from_user=User.model_construct(id=ADMIN, is_bot=False, first_name="A"),
        caption="/importwords alias",
        document=Document.model_construct(
            file_id="f", file_unique_id="u", file_size=99_000_000
        ),
    )
    await dispatcher.feed_update(
        bot, Update.model_construct(update_id=5, message=message)
    )

    assert "больше" in recording.sent_to(ADMIN)[-1]
    assert "GetFile" not in recording.method_names()


def test_words_pack_parsing() -> None:
    """пак слов читается из json и из текста с разделителями"""
    assert _parse_words_pack(b'["a", "b"]') == ["a", "b"]
    assert _parse_words_pack(b"a\nb, c") == ["a", "b", "c"]
    assert _parse_words_pack(b"") == []


def test_pair_parsing() -> None:
    """пара «название | описание» разбирается или отвергается"""
    assert _parse_pair("назв | опис") == ("назв", "опис")
    assert _parse_pair("без разделителя") is None
    assert _parse_pair("| пусто") is None
