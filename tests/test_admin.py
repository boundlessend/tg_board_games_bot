"""админка: доступ по id, сводка, отчёты файлом и управление контентом"""

from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    Chat,
    Document,
    Message,
    Update,
    User,
)

from constants import CB_ADMIN_ACTIVITY, CB_ADMIN_CSV, CB_ADMIN_STATS
from database import SQLiteHistoryStorage
from handlers.admin import CSV_CAPTION, ID_WARNING, create_admin_router
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


def _last_document(recording: RecordingSession) -> tuple[str, str]:
    """возвращает содержимое и подпись последнего отправленного документа"""
    name, payload = next(
        call for call in reversed(recording.calls) if call[0] == "SendDocument"
    )
    document = payload["document"]
    assert isinstance(document, BufferedInputFile)
    return document.data.decode("utf-8"), str(payload["caption"])


def _admin_dispatcher(
    storage: SQLiteHistoryStorage,
    word_games: list[WordGame],
) -> Dispatcher:
    """диспетчер с админскими роутерами"""
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_admin_router(storage, frozenset({ADMIN}), word_games)
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
    dispatcher = _admin_dispatcher(storage, word_games)

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
    dispatcher = _admin_dispatcher(storage, word_games)

    await _send(dispatcher, bot, ADMIN, "/admin", chat_type="supergroup")
    assert recording.calls == []


async def test_summary_counts_every_kind_of_issue(
    storage: SQLiteHistoryStorage,
    dangerous_content: DangerousWordsContent,
    word_games: list[WordGame],
) -> None:
    """сводка считает выдачи словесных игр: только они привязаны к человеку"""
    recording = RecordingSession()
    bot = make_bot(recording)
    dispatcher = _admin_dispatcher(storage, word_games)

    await storage.save_user_game_word(1, "alias", "альфа")
    await storage.save_user_game_word(2, "alias", "альфа")
    await storage.save_user_game_word(1, "crocodile", "бета")

    await _send(dispatcher, bot, ADMIN, "/admin")
    summary = recording.sent_to(ADMIN)[-1]
    assert "Пользователей: 2" in summary
    assert "Выданных слов: 3" in summary
    assert "альфа x2" in summary


async def test_full_report_counts_words_per_game(
    storage: SQLiteHistoryStorage,
    dangerous_content: DangerousWordsContent,
    word_games: list[WordGame],
) -> None:
    """подробный отчёт уходит документом со счётчиками по играм"""
    recording = RecordingSession()
    bot = make_bot(recording)
    dispatcher = _admin_dispatcher(storage, word_games)
    await storage.save_user_game_word(1, "alias", "альфа")
    await storage.save_user_game_word(1, "crocodile", "бета")

    await _press(dispatcher, bot, ADMIN, CB_ADMIN_STATS)
    report, caption = _last_document(recording)

    alias_pool = next(game for game in word_games if game.game_id == "alias").words
    assert "Статистика пользователя 1" in report
    assert f"alias: 1/{len(alias_pool)}" in report
    assert "crocodile: 1/" in report
    assert "альфа" in report and "бета" in report
    assert caption == f"Полный отчёт: пользователей 1.\n{ID_WARNING}"
    assert recording.sent_to(ADMIN) == []


async def test_csv_and_activity_reports(
    storage: SQLiteHistoryStorage,
    dangerous_content: DangerousWordsContent,
    word_games: list[WordGame],
) -> None:
    """csv отдаётся файлом с заголовком и строкой на игру, активность - текстом"""
    recording = RecordingSession()
    bot = make_bot(recording)
    dispatcher = _admin_dispatcher(storage, word_games)
    await storage.save_user_game_word(1, "alias", "альфа")
    await storage.save_user_game_word(1, "alias", "гамма")
    await storage.save_user_game_word(2, "crocodile", "бета")

    await _press(dispatcher, bot, ADMIN, CB_ADMIN_CSV)
    csv, caption = _last_document(recording)

    assert csv.splitlines() == [
        "telegram_id,game_id,words",
        "1,alias,2",
        "2,crocodile,1",
    ]
    assert caption == CSV_CAPTION

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
    dispatcher = _admin_dispatcher(storage, word_games)

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
    dispatcher = _admin_dispatcher(storage, word_games)

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
