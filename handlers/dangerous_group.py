import logging
import zlib
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, TelegramObject

from constants import (
    CB_DG_BOSS,
    CB_DG_BOSS_DROP,
    CB_DG_BOSS_KEEP,
    CB_DG_BOSS_REROLL,
    CB_DG_CARD_REROLL_PREFIX,
    CB_DG_CURSE,
    CB_DG_CURSE_DROP,
    CB_DG_CURSE_KEEP,
    CB_DG_CURSE_REROLL,
    CB_DG_EXPLAIN_PREFIX,
    CB_DG_FINISH,
    CB_DG_LEGACY_WORD_PREFIX,
    CB_DG_NEXT,
    CB_DG_OPEN,
    CB_DG_SEND,
    DANGEROUS_WORDS_GAME_ID,
    team_label,
)
from database import DatabaseError, SQLiteHistoryStorage
from handlers.common import (
    ChatLocks,
    data_startswith,
    event_chat_id,
    is_chat_manager,
    is_not_modified,
    lookup_chat_session,
    make_chat_lock_middleware,
    make_chat_persist_middleware,
    persist_session,
    restore_sessions,
)
from keyboards import (
    create_dangerous_group_keyboard,
    create_dg_offer_keyboard,
    create_dg_word_card_keyboard,
)
from services.content import Boss, Curse, DangerousWordsContent
from services.picking import pick_unique, pick_word

logger = logging.getLogger(__name__)

_SCOPE = "dangerous"


@dataclass
class DangerousGroup:
    """состояние партии «опасные слова»: по слову-дорожке на каждую команду

    обе команды играют одновременно. на команду t: words[t] - её секретное
    слово, explainer_ids[t] - объясняющий этой команды, sent[t] - доставлено
    ли слово объясняющему. слово команде t загадывает объясняющий соперников
    explainer_ids[1 - t]: он его знает, поэтому объяснять за t не может

    pending_curse_id и pending_boss_id держат ещё не принятое предложение:
    при рероле оно возвращается в пул, а «Убрать» снимает его целиком
    """

    host_id: int
    board_chat_id: int = 0
    board_message_id: int | None = None
    words: list[str | None] = field(default_factory=lambda: [None, None])
    explainer_ids: list[int | None] = field(default_factory=lambda: [None, None])
    explainer_names: list[str | None] = field(default_factory=lambda: [None, None])
    sent: list[bool] = field(default_factory=lambda: [False, False])
    boss_revealed: bool = False
    pending_curse_id: str | None = None
    pending_boss_id: str | None = None
    issued_words: set[str] = field(default_factory=set)
    issued_curses: set[str] = field(default_factory=set)
    issued_bosses: set[str] = field(default_factory=set)


def create_dangerous_group_router(
    content: DangerousWordsContent,
    storage: SQLiteHistoryStorage,
    sessions: dict[int, DangerousGroup],
) -> Router:
    """создаёт роутер командной игры «опасные слова» (бот-крупье)"""
    router = Router()
    locks = ChatLocks()

    async def _persist_chat(chat_id: int) -> None:
        await persist_chat_session(storage, sessions, chat_id)

    router.callback_query.middleware(make_chat_lock_middleware(locks, _session_chat_id))
    router.callback_query.middleware(
        make_chat_persist_middleware(_persist_chat, _SCOPE, _session_chat_id)
    )

    @router.callback_query(F.data == CB_DG_OPEN)
    async def handle_open(callback: CallbackQuery) -> None:
        """открывает поле командной партии в беседе"""
        message = callback.message
        if not isinstance(message, Message):
            await callback.answer()
            return
        if message.chat.type not in ("group", "supergroup"):
            await callback.answer(
                "Командные «Опасные слова» - в беседе.", show_alert=True
            )
            return
        session = sessions.get(message.chat.id)
        if session is None:
            session = DangerousGroup(
                host_id=callback.from_user.id, board_chat_id=message.chat.id
            )
            sessions[message.chat.id] = session
        else:
            # партия уже идёт: старое табло гасим, чтобы жил один комплект кнопок
            await _disable_board(callback.bot, session)
            session.board_chat_id = message.chat.id
        sent = await message.answer(
            _render_board(session),
            reply_markup=create_dangerous_group_keyboard(),
        )
        session.board_message_id = sent.message_id
        await callback.answer()

    @router.callback_query(data_startswith(CB_DG_EXPLAIN_PREFIX))
    async def handle_explain(callback: CallbackQuery) -> None:
        """назначает объясняющего команды и присылает ему слово для соперников

        объясняющий одной команды загадывает слово другой: оно приходит ему
        в личку, и его команда пишет к нему запретные. повторное нажатие
        отдаёт то же слово, а новый объясняющий получает новое: старое видел
        прежний, который мог нажать не за свою команду
        """
        session, chat_id = lookup_chat_session(callback, sessions)
        team = _parse_team(callback.data, CB_DG_EXPLAIN_PREFIX)
        bot = callback.bot
        if session is None or chat_id is None or team is None or bot is None:
            await callback.answer()
            return
        rival = 1 - team
        user_id = callback.from_user.id
        if session.explainer_ids[rival] == user_id:
            await callback.answer(
                f"Ты уже объясняешь за команду {rival + 1} и знаешь слово "
                f"команды {team + 1}.",
                show_alert=True,
            )
            return
        repeat = session.explainer_ids[team] == user_id
        word = session.words[rival]
        issued = session.issued_words
        if word is None or not repeat:
            pool = await _word_pool(callback, content, storage)
            if pool is None:
                return
            word, issued = pick_word(pool, session.issued_words)
        try:
            await bot.send_message(
                user_id,
                _riddle_text(rival, word),
                reply_markup=_riddle_keyboard(chat_id, rival, word),
            )
        except TelegramForbiddenError:
            await callback.answer(
                "Не дошло: нужен /start в личке с ботом.", show_alert=True
            )
            return
        session.words[rival] = word
        session.issued_words = issued
        if not repeat:
            # у нового объясняющего своего слова нет, а слово соперников
            # сменилось: обе дорожки нужно отправить заново
            session.sent[team] = False
            session.sent[rival] = False
        session.explainer_ids[team] = user_id
        session.explainer_names[team] = callback.from_user.full_name
        await _edit_board(callback, session)
        await callback.answer(
            f"Ты объясняешь за команду {team + 1}. Слово для соперников - в ЛС."
        )

    @router.callback_query(data_startswith(CB_DG_LEGACY_WORD_PREFIX))
    async def handle_legacy_word(callback: CallbackQuery) -> None:
        """отвечает на «Тянуть слово» со старого табло, поднятого из снапшота"""
        session, _ = lookup_chat_session(callback, sessions)
        if session is not None:
            await _edit_board(callback, session)
        await callback.answer(
            "Кнопка устарела: слово для соперников теперь приходит по «Я объясняющий».",
            show_alert=True,
        )

    # префикс ловит и «Отправить 1/2» (dg:send:0/1) со старых табло, поднятых из
    # снапшота: нажатие отправит слова и перерисует табло новой клавиатурой
    @router.callback_query(data_startswith(CB_DG_SEND))
    async def handle_send(callback: CallbackQuery) -> None:
        """отправляет слова обеих команд их объясняющим одним нажатием

        слова уходят обоим сразу, а объясняют их по очереди. кнопка ждёт
        готовности обеих дорожек и шлёт только тем, кому слово ещё не дошло
        """
        session, _ = lookup_chat_session(callback, sessions)
        if session is None:
            await callback.answer()
            return
        lanes = _ready_lanes(session)
        if len(lanes) < len(session.words):
            await callback.answer(_not_ready_text(session), show_alert=True)
            return
        if all(session.sent):
            await callback.answer("Слова уже у объясняющих.", show_alert=True)
            return
        bot = callback.bot
        if bot is None:
            await callback.answer()
            return
        failed: list[str] = []
        for team, word, explainer_id in lanes:
            if session.sent[team]:
                continue
            try:
                await bot.send_message(explainer_id, _explain_text(word))
            except TelegramForbiddenError:
                failed.append(team_label(team))
                continue
            session.sent[team] = True
        await _edit_board(callback, session)
        if failed:
            await callback.answer(
                f"Не дошло объясняющему ({', '.join(failed)}): "
                "нужен /start в личке с ботом.",
                show_alert=True,
            )
            return
        await callback.answer("Слова ушли объясняющим в ЛС.")

    @router.callback_query(data_startswith(CB_DG_CARD_REROLL_PREFIX))
    async def handle_card_reroll(callback: CallbackQuery) -> None:
        """меняет загаданное слово по кнопке под ним в личке загадывающего

        слово меняется только у него и только до отправки: «Отправить слова»
        отдаст объясняющему последнее. отвергнутое слово в пул не
        возвращается - его уже видели
        """
        card = _parse_card(callback.data)
        message = callback.message
        if card is None or not isinstance(message, Message):
            await callback.answer()
            return
        chat_id, team, tag = card
        # партии под id карточки может не быть и при живой игре: беседу
        # перевели в супергруппу, и партия переехала на новый id
        session = sessions.get(chat_id)
        if session is None or not _is_current_card(
            session, team, callback.from_user.id, tag
        ):
            await callback.answer(
                "Карточка устарела: слово уже сменилось или раунд закончился.",
                show_alert=True,
            )
            return
        if session.sent[team]:
            await callback.answer(
                "Слово уже у объясняющего: реролл закрыт.", show_alert=True
            )
            return
        pool = await _word_pool(callback, content, storage)
        if pool is None:
            return
        word, issued = pick_word(pool, session.issued_words)
        try:
            await message.edit_text(
                _riddle_text(team, word),
                reply_markup=_riddle_keyboard(chat_id, team, word),
            )
        except TelegramBadRequest as error:
            # круг слов пройден и выпало то же слово: карточка и так верна
            if not is_not_modified(error):
                raise
        session.words[team] = word
        session.issued_words = issued
        await callback.answer("Новое слово.")

    @router.callback_query(F.data == CB_DG_NEXT)
    async def handle_new_round(callback: CallbackQuery) -> None:
        """сбрасывает обе дорожки для нового раунда - только ведущий"""
        session, chat_id = lookup_chat_session(callback, sessions)
        if session is None or chat_id is None:
            await callback.answer()
            return
        if not await _may_manage(callback, session, chat_id):
            await callback.answer(
                "Новый раунд запускает ведущий или админ чата.", show_alert=True
            )
            return
        session.words = [None, None]
        session.explainer_ids = [None, None]
        session.explainer_names = [None, None]
        session.sent = [False, False]
        await _edit_board(callback, session)
        await callback.answer("Новый раунд: выберите объясняющих заново.")

    @router.callback_query(F.data == CB_DG_CURSE)
    async def handle_curse(callback: CallbackQuery) -> None:
        """тянет проклятие и предлагает принять или реролльнуть (ведущий)"""
        session, chat_id = lookup_chat_session(callback, sessions)
        message = callback.message
        if session is None or chat_id is None or not isinstance(message, Message):
            await callback.answer()
            return
        if not await _may_manage(callback, session, chat_id):
            await callback.answer(
                "Проклятие тянет ведущий или админ чата.", show_alert=True
            )
            return
        curse = await _draw_curse(callback, session, content, storage)
        if curse is None:
            return
        await message.answer(
            _curse_text(curse),
            reply_markup=_offer_keyboard(
                CB_DG_CURSE_KEEP, CB_DG_CURSE_REROLL, CB_DG_CURSE_DROP, curse.id
            ),
        )
        await callback.answer()

    @router.callback_query(data_startswith(CB_DG_CURSE_REROLL))
    async def handle_curse_reroll(callback: CallbackQuery) -> None:
        """заменяет предложенное проклятие новым (только ведущий)"""
        session, chat_id = lookup_chat_session(callback, sessions)
        message = callback.message
        if session is None or chat_id is None or not isinstance(message, Message):
            await callback.answer()
            return
        if not await _may_manage(callback, session, chat_id):
            await callback.answer(
                "Реролл делает ведущий или админ чата.", show_alert=True
            )
            return
        if not _is_current_offer(
            callback.data, CB_DG_CURSE_REROLL, session.pending_curse_id
        ):
            await _answer_stale(callback)
            return
        # отвергнутое проклятие возвращается в пул этой партии
        _return_to_pool(session.issued_curses, session.pending_curse_id)
        curse = await _draw_curse(callback, session, content, storage)
        if curse is None:
            return
        try:
            await message.edit_text(
                _curse_text(curse),
                reply_markup=_offer_keyboard(
                    CB_DG_CURSE_KEEP, CB_DG_CURSE_REROLL, CB_DG_CURSE_DROP, curse.id
                ),
            )
        except TelegramBadRequest:
            pass
        await callback.answer("Новое проклятие.")

    @router.callback_query(data_startswith(CB_DG_CURSE_KEEP))
    async def handle_curse_keep(callback: CallbackQuery) -> None:
        """фиксирует проклятие в чате, убирая кнопки (только ведущий)"""
        session, chat_id = lookup_chat_session(callback, sessions)
        if session is None or chat_id is None:
            await callback.answer()
            return
        if not await _may_manage(callback, session, chat_id):
            await callback.answer("Принимает ведущий или админ чата.", show_alert=True)
            return
        if not _is_current_offer(
            callback.data, CB_DG_CURSE_KEEP, session.pending_curse_id
        ):
            await _answer_stale(callback)
            return
        session.pending_curse_id = None
        await _strip_offer_keyboard(callback)
        await callback.answer("Проклятие принято.")

    @router.callback_query(data_startswith(CB_DG_CURSE_DROP))
    async def handle_curse_drop(callback: CallbackQuery) -> None:
        """снимает предложенное проклятие и возвращает его в пул"""
        session, chat_id = lookup_chat_session(callback, sessions)
        if session is None or chat_id is None:
            await callback.answer()
            return
        if not await _may_manage(callback, session, chat_id):
            await callback.answer("Убирает ведущий или админ чата.", show_alert=True)
            return
        if not _is_current_offer(
            callback.data, CB_DG_CURSE_DROP, session.pending_curse_id
        ):
            await _answer_stale(callback)
            return
        _return_to_pool(session.issued_curses, session.pending_curse_id)
        session.pending_curse_id = None
        await _drop_offer_message(callback, "Проклятие убрано.")
        await callback.answer("Проклятие убрано.")

    @router.callback_query(F.data == CB_DG_BOSS)
    async def handle_boss(callback: CallbackQuery) -> None:
        """тянет босса и предлагает принять или реролльнуть (ведущий)"""
        session, chat_id = lookup_chat_session(callback, sessions)
        message = callback.message
        if session is None or chat_id is None or not isinstance(message, Message):
            await callback.answer()
            return
        if not await _may_manage(callback, session, chat_id):
            await callback.answer(
                "Босса тянет ведущий или админ чата.", show_alert=True
            )
            return
        if session.boss_revealed:
            await callback.answer(
                "Босс уже раскрыт - он один на игру.", show_alert=True
            )
            return
        if session.pending_boss_id is not None:
            await callback.answer(
                "Босс уже на столе: примите, реролльните или уберите его.",
                show_alert=True,
            )
            return
        boss = await _draw_boss(callback, session, content, storage)
        if boss is None:
            return
        await message.answer(
            _boss_text(boss),
            reply_markup=_offer_keyboard(
                CB_DG_BOSS_KEEP, CB_DG_BOSS_REROLL, CB_DG_BOSS_DROP, boss.id
            ),
        )
        await callback.answer()

    @router.callback_query(data_startswith(CB_DG_BOSS_REROLL))
    async def handle_boss_reroll(callback: CallbackQuery) -> None:
        """заменяет предложенного босса новым (только ведущий)"""
        session, chat_id = lookup_chat_session(callback, sessions)
        message = callback.message
        if session is None or chat_id is None or not isinstance(message, Message):
            await callback.answer()
            return
        if not await _may_manage(callback, session, chat_id):
            await callback.answer(
                "Реролл делает ведущий или админ чата.", show_alert=True
            )
            return
        if not _is_current_offer(
            callback.data, CB_DG_BOSS_REROLL, session.pending_boss_id
        ):
            await _answer_stale(callback)
            return
        _return_to_pool(session.issued_bosses, session.pending_boss_id)
        boss = await _draw_boss(callback, session, content, storage)
        if boss is None:
            return
        try:
            await message.edit_text(
                _boss_text(boss),
                reply_markup=_offer_keyboard(
                    CB_DG_BOSS_KEEP, CB_DG_BOSS_REROLL, CB_DG_BOSS_DROP, boss.id
                ),
            )
        except TelegramBadRequest:
            pass
        await callback.answer("Новый босс.")

    @router.callback_query(data_startswith(CB_DG_BOSS_KEEP))
    async def handle_boss_keep(callback: CallbackQuery) -> None:
        """фиксирует босса на игру, убирая кнопки (только ведущий)"""
        session, chat_id = lookup_chat_session(callback, sessions)
        if session is None or chat_id is None:
            await callback.answer()
            return
        if not await _may_manage(callback, session, chat_id):
            await callback.answer("Принимает ведущий или админ чата.", show_alert=True)
            return
        if not _is_current_offer(
            callback.data, CB_DG_BOSS_KEEP, session.pending_boss_id
        ):
            await _answer_stale(callback)
            return
        session.boss_revealed = True
        session.pending_boss_id = None
        await _strip_offer_keyboard(callback)
        await _refresh_board(callback.bot, session)
        await callback.answer("Босс зафиксирован на игру.")

    @router.callback_query(data_startswith(CB_DG_BOSS_DROP))
    async def handle_boss_drop(callback: CallbackQuery) -> None:
        """снимает предложенного босса и возвращает его в колоду"""
        session, chat_id = lookup_chat_session(callback, sessions)
        if session is None or chat_id is None:
            await callback.answer()
            return
        if not await _may_manage(callback, session, chat_id):
            await callback.answer("Убирает ведущий или админ чата.", show_alert=True)
            return
        if not _is_current_offer(
            callback.data, CB_DG_BOSS_DROP, session.pending_boss_id
        ):
            await _answer_stale(callback)
            return
        _return_to_pool(session.issued_bosses, session.pending_boss_id)
        session.pending_boss_id = None
        await _drop_offer_message(callback, "Босс убран, можно тянуть заново.")
        await callback.answer("Босс убран.")

    @router.callback_query(F.data == CB_DG_FINISH)
    async def handle_finish(callback: CallbackQuery) -> None:
        """завершает партию (ведущий или админ чата)"""
        session, chat_id = lookup_chat_session(callback, sessions)
        if session is None or chat_id is None:
            await callback.answer()
            return
        if not await _may_manage(callback, session, chat_id):
            await callback.answer("Завершает ведущий или админ чата.", show_alert=True)
            return
        sessions.pop(chat_id, None)
        message = callback.message
        if isinstance(message, Message):
            try:
                await message.edit_text("«Опасные слова»: игра окончена.")
            except TelegramBadRequest:
                pass
        await callback.answer()

    async def _may_manage(
        callback: CallbackQuery, session: DangerousGroup, chat_id: int
    ) -> bool:
        """проверяет право распоряжаться партией"""
        bot = callback.bot
        if bot is None:
            return callback.from_user.id == session.host_id
        return await is_chat_manager(
            bot, chat_id, callback.from_user.id, session.host_id
        )

    return router


async def _word_pool(
    callback: CallbackQuery,
    content: DangerousWordsContent,
    storage: SQLiteHistoryStorage,
) -> list[str] | None:
    """собирает пул слов партии без дублей, при сбое базы отвечая игроку"""
    try:
        custom = await storage.get_custom_words(DANGEROUS_WORDS_GAME_ID)
    except DatabaseError:
        logger.exception("database_error", extra={"action": "dg_word"})
        await callback.answer("Ошибка БД. Попробуй позже.", show_alert=True)
        return None
    return list(dict.fromkeys(content.words + custom))


def _ready_lanes(session: DangerousGroup) -> list[tuple[int, str, int]]:
    """дорожки, готовые к отправке: команда, её слово и объясняющий"""
    return [
        (team, word, explainer_id)
        for team, (word, explainer_id) in enumerate(
            zip(session.words, session.explainer_ids, strict=True)
        )
        if word is not None and explainer_id is not None
    ]


def _not_ready_text(session: DangerousGroup) -> str:
    """объясняет, чего не хватает для отправки слов

    слово команде загадывает объясняющий соперников, поэтому пока не выбраны
    оба объясняющих, про слова говорить рано
    """
    missing = [
        str(team + 1) for team in range(2) if session.explainer_ids[team] is None
    ]
    if not missing:
        # оба объясняющих есть, а слова нет только у партии из старого снапшота
        return (
            "Не всем командам загадано слово: объясняющие, нажмите свою кнопку ещё раз."
        )
    return (
        "Слова уходят обоим объясняющим сразу. "
        f"Не выбран объясняющий у команды {' и '.join(missing)}."
    )


def _explain_text(word: str) -> str:
    """текст слова, которое объясняющий объясняет своей команде"""
    return f"Слово для объяснения: {word}"


def _riddle_text(team: int, word: str) -> str:
    """текст карточки загадывающего: слово для команды соперников"""
    return (
        f"Загадай слово команде {team + 1}: {word}\n"
        "Напишите запретные слова, затем «Отправить слова»."
    )


def _riddle_keyboard(chat_id: int, team: int, word: str) -> InlineKeyboardMarkup:
    """кнопка реролла, привязанная к партии, команде и конкретному слову"""
    return create_dg_word_card_keyboard(
        f"{CB_DG_CARD_REROLL_PREFIX}{chat_id}:{team}:{_word_tag(word)}"
    )


def _word_tag(word: str) -> str:
    """короткая метка слова для кнопки: callback_data ограничена 64 байтами

    по метке старые карточки отличаются от карточки текущего слова
    """
    return f"{zlib.crc32(word.encode()):08x}"


def _parse_card(data: str | None) -> tuple[int, int, str] | None:
    """разбирает кнопку карточки: чат партии, команда и метка слова"""
    if data is None or not data.startswith(CB_DG_CARD_REROLL_PREFIX):
        return None
    parts = data[len(CB_DG_CARD_REROLL_PREFIX) :].split(":")
    if len(parts) != 3:
        return None
    chat_part, team_part, tag = parts
    team = _parse_team(team_part, "")
    try:
        chat_id = int(chat_part)
    except ValueError:
        return None
    if team is None:
        return None
    return chat_id, team, tag


def _is_current_card(
    session: DangerousGroup, team: int, user_id: int, tag: str
) -> bool:
    """карточка жива, пока слово загадывает этот игрок и оно не сменилось"""
    word = session.words[team]
    return (
        word is not None
        and session.explainer_ids[1 - team] == user_id
        and _word_tag(word) == tag
    )


def _session_chat_id(event: TelegramObject) -> int | None:
    """чат партии события: карточка слова лежит в личке, а партия - в беседе

    блокировка и снапшот должны браться по беседе, иначе реролл из лички
    разошёлся бы с нажатиями на табло и не попал бы в снапшот
    """
    if isinstance(event, CallbackQuery):
        card = _parse_card(event.data)
        if card is not None:
            return card[0]
    return event_chat_id(event)


async def _draw_curse(
    callback: CallbackQuery,
    session: DangerousGroup,
    content: DangerousWordsContent,
    storage: SQLiteHistoryStorage,
) -> Curse | None:
    """тянет проклятие из пула и запоминает его как предложенное"""
    try:
        pool = content.curses + await storage.get_custom_curses()
    except DatabaseError:
        logger.exception("database_error", extra={"action": "dg_curse"})
        await callback.answer("Ошибка БД. Попробуй позже.", show_alert=True)
        return None

    drawn = pick_unique(pool, session.issued_curses, lambda c: c.id)
    if drawn is None:
        await callback.answer("Проклятий нет.", show_alert=True)
        return None
    curse, session.issued_curses = drawn
    session.pending_curse_id = curse.id
    return curse


async def _draw_boss(
    callback: CallbackQuery,
    session: DangerousGroup,
    content: DangerousWordsContent,
    storage: SQLiteHistoryStorage,
) -> Boss | None:
    """тянет босса из пула и запоминает его как предложенного"""
    try:
        pool = content.bosses + await storage.get_custom_bosses()
    except DatabaseError:
        logger.exception("database_error", extra={"action": "dg_boss"})
        await callback.answer("Ошибка БД. Попробуй позже.", show_alert=True)
        return None

    drawn = pick_unique(pool, session.issued_bosses, lambda b: b.id)
    if drawn is None:
        await callback.answer("Боссов нет.", show_alert=True)
        return None
    boss, session.issued_bosses = drawn
    session.pending_boss_id = boss.id
    return boss


def _offer_keyboard(
    keep_data: str, reroll_data: str, drop_data: str, offer_id: str
) -> InlineKeyboardMarkup:
    """клавиатура предложения с привязкой кнопок к конкретной карте"""
    return create_dg_offer_keyboard(
        f"{keep_data}:{offer_id}",
        f"{reroll_data}:{offer_id}",
        f"{drop_data}:{offer_id}",
    )


def _is_current_offer(data: str | None, action: str, pending_id: str | None) -> bool:
    """проверяет, что кнопка нажата у актуального предложения партии"""
    if data is None or pending_id is None:
        return False
    return data == f"{action}:{pending_id}"


async def _answer_stale(callback: CallbackQuery) -> None:
    """сообщает, что предложение устарело и кнопки на нём мертвы"""
    await callback.answer(
        "Это предложение устарело: работайте с последним.", show_alert=True
    )


def _return_to_pool(issued: set[str], item_id: str | None) -> None:
    """возвращает отвергнутый элемент в пул партии"""
    if item_id is not None:
        issued.discard(item_id)


async def _strip_offer_keyboard(callback: CallbackQuery) -> None:
    """убирает кнопки у принятого предложения, оставляя текст в чате"""
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass


async def _drop_offer_message(callback: CallbackQuery, text: str) -> None:
    """заменяет снятое предложение короткой пометкой"""
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text)
        except TelegramBadRequest:
            pass


def _dump_session(session: DangerousGroup) -> dict[str, Any]:
    """сериализует партию «опасные слова» в словарь"""
    return {
        "host_id": session.host_id,
        "board_chat_id": session.board_chat_id,
        "board_message_id": session.board_message_id,
        "words": session.words,
        "explainer_ids": session.explainer_ids,
        "explainer_names": session.explainer_names,
        "sent": session.sent,
        "boss_revealed": session.boss_revealed,
        "pending_curse_id": session.pending_curse_id,
        "pending_boss_id": session.pending_boss_id,
        "issued_words": list(session.issued_words),
        "issued_curses": list(session.issued_curses),
        "issued_bosses": list(session.issued_bosses),
    }


def _load_session(data: dict[str, Any]) -> DangerousGroup:
    """восстанавливает партию «опасные слова» из словаря"""
    return DangerousGroup(
        host_id=data["host_id"],
        board_chat_id=data.get("board_chat_id", 0),
        board_message_id=data.get("board_message_id"),
        words=list(data["words"]),
        explainer_ids=list(data["explainer_ids"]),
        explainer_names=list(data["explainer_names"]),
        sent=list(data["sent"]),
        boss_revealed=data["boss_revealed"],
        pending_curse_id=data.get("pending_curse_id"),
        pending_boss_id=data.get("pending_boss_id"),
        issued_words=set(data["issued_words"]),
        issued_curses=set(data["issued_curses"]),
        issued_bosses=set(data["issued_bosses"]),
    )


async def persist_chat_session(
    storage: SQLiteHistoryStorage,
    sessions: dict[int, DangerousGroup],
    chat_id: int,
) -> None:
    """сохраняет или удаляет снапшот партии одного чата"""
    await persist_session(
        storage, _SCOPE, str(chat_id), sessions.get(chat_id), _dump_session
    )


async def restore_dangerous_sessions(
    storage: SQLiteHistoryStorage, sessions: dict[int, DangerousGroup]
) -> None:
    """наполняет словарь партий снапшотами из хранилища при старте"""
    sessions.update(await restore_sessions(storage, _SCOPE, int, _load_session))


async def _edit_board(callback: CallbackQuery, session: DangerousGroup) -> None:
    """перерисовывает поле партии на месте"""
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(
                _render_board(session),
                reply_markup=create_dangerous_group_keyboard(),
            )
        except TelegramBadRequest:
            pass


async def _disable_board(bot: Bot | None, session: DangerousGroup) -> None:
    """снимает клавиатуру со старого табло партии"""
    if bot is None or session.board_message_id is None or not session.board_chat_id:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=session.board_chat_id,
            message_id=session.board_message_id,
            reply_markup=None,
        )
    except TelegramBadRequest:
        pass


async def _refresh_board(bot: Bot | None, session: DangerousGroup) -> None:
    """обновляет табло по запомненному сообщению, а не по месту нажатия

    нужно, когда действие происходит в отдельном сообщении (предложение
    босса), а поменялся статус на табло
    """
    if bot is None or session.board_message_id is None:
        return
    try:
        await bot.edit_message_text(
            _render_board(session),
            chat_id=session.board_chat_id,
            message_id=session.board_message_id,
            reply_markup=create_dangerous_group_keyboard(),
        )
    except TelegramBadRequest:
        pass


def _parse_team(data: str | None, prefix: str) -> int | None:
    """извлекает индекс команды (0 или 1) из callback-данных"""
    if data is None:
        return None
    suffix = data[len(prefix) :]
    if suffix == "0":
        return 0
    if suffix == "1":
        return 1
    return None


def _curse_text(curse: Curse) -> str:
    """текст сообщения с проклятием"""
    return f"Проклятие: {curse.title}\n{curse.description}"


def _boss_text(boss: Boss) -> str:
    """текст сообщения с боссом"""
    return f"Босс (финал): {boss.name}\n{boss.description}"


def _render_board(session: DangerousGroup) -> str:
    """рисует поле партии «опасные слова»: статус обеих дорожек"""
    boss = "раскрыт" if session.boss_revealed else "в колоде (финал)"
    lines = [
        "Опасные слова - обе команды играют одновременно.",
        "Объясняющий загадывает слово соперникам (придёт ему в ЛС), его "
        "команда пишет запретные. «Отправить слова» раздаёт слова обоим "
        "объясняющим, объясняют по очереди.",
        "",
    ]
    for team in range(2):
        word_state = "загадано" if session.words[team] else "не загадано"
        explainer = session.explainer_names[team] or "не выбран"
        sent_state = "отправлено" if session.sent[team] else "не отправлено"
        lines.append(
            f"{team_label(team)}: слово {word_state}, "
            f"объясняющий {explainer}, {sent_state}."
        )
    lines.extend(["", f"Босс: {boss}."])
    return "\n".join(lines)
