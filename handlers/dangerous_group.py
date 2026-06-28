import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, Message

from constants import (
    CB_DG_BOSS,
    CB_DG_BOSS_KEEP,
    CB_DG_BOSS_REROLL,
    CB_DG_CURSE,
    CB_DG_CURSE_KEEP,
    CB_DG_CURSE_REROLL,
    CB_DG_EXPLAIN_PREFIX,
    CB_DG_FINISH,
    CB_DG_NEXT,
    CB_DG_OPEN,
    CB_DG_SEND_PREFIX,
    CB_DG_WORD_PREFIX,
    DANGEROUS_WORDS_GAME_ID,
    team_label,
)
from database import DatabaseError, SQLiteHistoryStorage
from handlers.common import (
    data_startswith,
    lookup_chat_session,
    make_chat_lock_middleware,
    make_persist_middleware,
    pick_unique,
    pick_word,
)
from keyboards import (
    create_dangerous_group_keyboard,
    create_dg_offer_keyboard,
)
from services.random_generator import Boss, Curse, DangerousWordsContent

logger = logging.getLogger(__name__)

_SCOPE = "dangerous"


@dataclass
class DangerousGroup:
    """состояние партии «опасные слова»: по слову-дорожке на каждую команду

    обе команды играют одновременно. на команду t: words[t] - её секретное
    слово (соперники тянут его и пишут запретные), explainer_ids[t] -
    объясняющий этой команды, sent[t] - доставлено ли слово объясняющему
    """

    host_id: int
    words: list[str | None] = field(default_factory=lambda: [None, None])
    explainer_ids: list[int | None] = field(
        default_factory=lambda: [None, None]
    )
    explainer_names: list[str | None] = field(
        default_factory=lambda: [None, None]
    )
    sent: list[bool] = field(default_factory=lambda: [False, False])
    boss_revealed: bool = False
    boss_pending: bool = False
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
    locks: dict[int, asyncio.Lock] = {}

    async def _persist() -> None:
        await _persist_sessions(storage, sessions)

    router.callback_query.middleware(make_chat_lock_middleware(locks))
    router.callback_query.middleware(
        make_persist_middleware(_persist, _SCOPE)
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
        session = DangerousGroup(host_id=callback.from_user.id)
        sessions[message.chat.id] = session
        await message.answer(
            _render_board(session),
            reply_markup=create_dangerous_group_keyboard(),
        )
        await callback.answer()

    @router.callback_query(data_startswith(CB_DG_EXPLAIN_PREFIX))
    async def handle_explain(callback: CallbackQuery) -> None:
        """назначает объясняющего команды (он объясняет слово своей команде)"""
        session, _ = lookup_chat_session(callback, sessions)
        team = _parse_team(callback.data, CB_DG_EXPLAIN_PREFIX)
        if session is None or team is None:
            await callback.answer()
            return
        session.explainer_ids[team] = callback.from_user.id
        session.explainer_names[team] = callback.from_user.full_name
        await _edit_board(callback, session)
        await callback.answer(f"Ты объясняешь за {team_label(team)}.")

    @router.callback_query(data_startswith(CB_DG_WORD_PREFIX))
    async def handle_word(callback: CallbackQuery) -> None:
        """тянет секретное слово команды в ЛС соперникам - писать запретные"""
        session, _ = lookup_chat_session(callback, sessions)
        team = _parse_team(callback.data, CB_DG_WORD_PREFIX)
        if session is None or team is None:
            await callback.answer()
            return
        bot = callback.bot
        if bot is None:
            await callback.answer()
            return
        try:
            pool = list(
                dict.fromkeys(
                    content.words
                    + await storage.get_custom_words(DANGEROUS_WORDS_GAME_ID)
                )
            )
        except DatabaseError:
            logger.exception("database_error", extra={"action": "dg_word"})
            await callback.answer("Ошибка БД. Попробуйте позже.", show_alert=True)
            return

        word = pick_word(pool, session.issued_words)
        session.words[team] = word
        session.sent[team] = False
        try:
            await bot.send_message(
                callback.from_user.id,
                f"Слово {team_label(team)}: {word}\n"
                "Напишите запретные слова, затем «отправить».",
            )
        except TelegramForbiddenError:
            await callback.answer(
                "Не дошло: нужен /start в личке с ботом.", show_alert=True
            )
            return
        await _edit_board(callback, session)
        await callback.answer("Слово в ЛС: напишите запретные.")

    @router.callback_query(data_startswith(CB_DG_SEND_PREFIX))
    async def handle_send(callback: CallbackQuery) -> None:
        """отправляет секретное слово команды её объясняющему"""
        session, _ = lookup_chat_session(callback, sessions)
        team = _parse_team(callback.data, CB_DG_SEND_PREFIX)
        if session is None or team is None:
            await callback.answer()
            return
        word = session.words[team]
        explainer_id = session.explainer_ids[team]
        if word is None:
            await callback.answer(
                "Сначала вытяните слово этой команды.", show_alert=True
            )
            return
        if explainer_id is None:
            await callback.answer(
                f"Объясняющий {team_label(team)} не выбран («объясняю»).",
                show_alert=True,
            )
            return
        bot = callback.bot
        if bot is None:
            await callback.answer()
            return
        try:
            await bot.send_message(
                explainer_id, f"Слово для объяснения: {word}"
            )
        except TelegramForbiddenError:
            await callback.answer(
                "Объясняющему не дошло: нужен /start в личке с ботом.",
                show_alert=True,
            )
            return
        session.sent[team] = True
        await _edit_board(callback, session)
        await callback.answer("Слово ушло объясняющему в ЛС.")

    @router.callback_query(F.data == CB_DG_NEXT)
    async def handle_new_round(callback: CallbackQuery) -> None:
        """сбрасывает обе дорожки для нового раунда - только ведущий"""
        session, _ = lookup_chat_session(callback, sessions)
        if session is None:
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer(
                "Новый раунд запускает ведущий.", show_alert=True
            )
            return
        session.words = [None, None]
        session.explainer_ids = [None, None]
        session.explainer_names = [None, None]
        session.sent = [False, False]
        await _edit_board(callback, session)
        await callback.answer("Новый раунд: тяните слова заново.")

    @router.callback_query(F.data == CB_DG_CURSE)
    async def handle_curse(callback: CallbackQuery) -> None:
        """тянет проклятие и предлагает принять или реролльнуть (ведущий)"""
        session, _ = lookup_chat_session(callback, sessions)
        message = callback.message
        if session is None or not isinstance(message, Message):
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer(
                "Проклятие тянет ведущий (кто открыл игру).", show_alert=True
            )
            return
        try:
            pool = content.curses + await storage.get_custom_curses()
        except DatabaseError:
            logger.exception("database_error", extra={"action": "dg_curse"})
            await callback.answer("Ошибка БД. Попробуйте позже.", show_alert=True)
            return

        curse = pick_unique(pool, session.issued_curses, lambda c: c.id)
        if curse is None:
            await callback.answer("Проклятий нет.", show_alert=True)
            return
        await message.answer(
            _curse_text(curse),
            reply_markup=create_dg_offer_keyboard(
                CB_DG_CURSE_KEEP, CB_DG_CURSE_REROLL
            ),
        )
        await callback.answer()

    @router.callback_query(F.data == CB_DG_CURSE_REROLL)
    async def handle_curse_reroll(callback: CallbackQuery) -> None:
        """заменяет предложенное проклятие новым (только ведущий)"""
        session, _ = lookup_chat_session(callback, sessions)
        message = callback.message
        if session is None or not isinstance(message, Message):
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Реролл делает ведущий.", show_alert=True)
            return
        try:
            pool = content.curses + await storage.get_custom_curses()
        except DatabaseError:
            logger.exception("database_error", extra={"action": "dg_curse"})
            await callback.answer("Ошибка БД. Попробуйте позже.", show_alert=True)
            return

        curse = pick_unique(pool, session.issued_curses, lambda c: c.id)
        if curse is None:
            await callback.answer("Проклятий нет.", show_alert=True)
            return
        try:
            await message.edit_text(
                _curse_text(curse),
                reply_markup=create_dg_offer_keyboard(
                    CB_DG_CURSE_KEEP, CB_DG_CURSE_REROLL
                ),
            )
        except TelegramBadRequest:
            pass
        await callback.answer("Новое проклятие.")

    @router.callback_query(F.data == CB_DG_CURSE_KEEP)
    async def handle_curse_keep(callback: CallbackQuery) -> None:
        """фиксирует проклятие в чате, убирая кнопки (только ведущий)"""
        session, _ = lookup_chat_session(callback, sessions)
        message = callback.message
        if session is None or not isinstance(message, Message):
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Принимает ведущий.", show_alert=True)
            return
        try:
            await message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.answer("Проклятие принято.")

    @router.callback_query(F.data == CB_DG_BOSS)
    async def handle_boss(callback: CallbackQuery) -> None:
        """тянет босса и предлагает принять или реролльнуть (ведущий)"""
        session, _ = lookup_chat_session(callback, sessions)
        message = callback.message
        if session is None or not isinstance(message, Message):
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Босса тянет ведущий.", show_alert=True)
            return
        if session.boss_revealed:
            await callback.answer(
                "Босс уже раскрыт - он один на игру.", show_alert=True
            )
            return
        if session.boss_pending:
            await callback.answer(
                "Босс уже на столе: примите или реролльните.", show_alert=True
            )
            return
        try:
            pool = content.bosses + await storage.get_custom_bosses()
        except DatabaseError:
            logger.exception("database_error", extra={"action": "dg_boss"})
            await callback.answer("Ошибка БД. Попробуйте позже.", show_alert=True)
            return

        boss = pick_unique(pool, session.issued_bosses, lambda b: b.id)
        if boss is None:
            await callback.answer("Боссов нет.", show_alert=True)
            return
        session.boss_pending = True
        await message.answer(
            _boss_text(boss),
            reply_markup=create_dg_offer_keyboard(
                CB_DG_BOSS_KEEP, CB_DG_BOSS_REROLL
            ),
        )
        await callback.answer()

    @router.callback_query(F.data == CB_DG_BOSS_REROLL)
    async def handle_boss_reroll(callback: CallbackQuery) -> None:
        """заменяет предложенного босса новым (только ведущий)"""
        session, _ = lookup_chat_session(callback, sessions)
        message = callback.message
        if session is None or not isinstance(message, Message):
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Реролл делает ведущий.", show_alert=True)
            return
        try:
            pool = content.bosses + await storage.get_custom_bosses()
        except DatabaseError:
            logger.exception("database_error", extra={"action": "dg_boss"})
            await callback.answer("Ошибка БД. Попробуйте позже.", show_alert=True)
            return

        boss = pick_unique(pool, session.issued_bosses, lambda b: b.id)
        if boss is None:
            await callback.answer("Боссов нет.", show_alert=True)
            return
        try:
            await message.edit_text(
                _boss_text(boss),
                reply_markup=create_dg_offer_keyboard(
                    CB_DG_BOSS_KEEP, CB_DG_BOSS_REROLL
                ),
            )
        except TelegramBadRequest:
            pass
        await callback.answer("Новый босс.")

    @router.callback_query(F.data == CB_DG_BOSS_KEEP)
    async def handle_boss_keep(callback: CallbackQuery) -> None:
        """фиксирует босса на игру, убирая кнопки (только ведущий)"""
        session, _ = lookup_chat_session(callback, sessions)
        message = callback.message
        if session is None or not isinstance(message, Message):
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Принимает ведущий.", show_alert=True)
            return
        session.boss_revealed = True
        session.boss_pending = False
        # ponytail: статус босса на табло обновится при следующем рендере
        try:
            await message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.answer("Босс зафиксирован на игру.")

    @router.callback_query(F.data == CB_DG_FINISH)
    async def handle_finish(callback: CallbackQuery) -> None:
        """завершает партию (только ведущий)"""
        session, chat_id = lookup_chat_session(callback, sessions)
        if session is None or chat_id is None:
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Завершает ведущий.", show_alert=True)
            return
        sessions.pop(chat_id, None)
        message = callback.message
        if isinstance(message, Message):
            try:
                await message.edit_text("«Опасные слова»: игра окончена.")
            except TelegramBadRequest:
                pass
        await callback.answer()

    return router


def _dump_session(session: DangerousGroup) -> dict[str, Any]:
    """сериализует партию «опасные слова» в словарь"""
    return {
        "host_id": session.host_id,
        "words": session.words,
        "explainer_ids": session.explainer_ids,
        "explainer_names": session.explainer_names,
        "sent": session.sent,
        "boss_revealed": session.boss_revealed,
        "boss_pending": session.boss_pending,
        "issued_words": list(session.issued_words),
        "issued_curses": list(session.issued_curses),
        "issued_bosses": list(session.issued_bosses),
    }


def _load_session(data: dict[str, Any]) -> DangerousGroup:
    """восстанавливает партию «опасные слова» из словаря"""
    return DangerousGroup(
        host_id=data["host_id"],
        words=list(data["words"]),
        explainer_ids=list(data["explainer_ids"]),
        explainer_names=list(data["explainer_names"]),
        sent=list(data["sent"]),
        boss_revealed=data["boss_revealed"],
        boss_pending=data["boss_pending"],
        issued_words=set(data["issued_words"]),
        issued_curses=set(data["issued_curses"]),
        issued_bosses=set(data["issued_bosses"]),
    )


async def _persist_sessions(
    storage: SQLiteHistoryStorage, sessions: dict[int, DangerousGroup]
) -> None:
    """сохраняет снапшот всех партий «опасные слова»"""
    items = {
        str(chat_id): json.dumps(_dump_session(session))
        for chat_id, session in sessions.items()
    }
    await storage.replace_session_scope(_SCOPE, items)


async def restore_dangerous_sessions(
    storage: SQLiteHistoryStorage, sessions: dict[int, DangerousGroup]
) -> None:
    """наполняет словарь партий снапшотами из хранилища при старте"""
    raw = await storage.load_session_scope(_SCOPE)
    for key, data in raw.items():
        try:
            sessions[int(key)] = _load_session(json.loads(data))
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            # снапшот несовместимой/повреждённой схемы - пропускаем
            logger.exception(
                "session_restore_failed",
                extra={"scope": _SCOPE, "key": key},
            )


async def _edit_board(
    callback: CallbackQuery, session: DangerousGroup
) -> None:
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


def _parse_team(data: str | None, prefix: str) -> int | None:
    """извлекает индекс команды (0 или 1) из callback-данных"""
    if data is None:
        return None
    suffix = data[len(prefix):]
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
        "Соперники тянут секретное слово команды (придёт им в ЛС) и пишут "
        "запретные; объясняющий команды объясняет его своим.",
        "",
    ]
    for team in range(2):
        word_state = "взято" if session.words[team] else "не взято"
        explainer = session.explainer_names[team] or "не выбран"
        sent_state = "отправлено" if session.sent[team] else "не отправлено"
        lines.append(
            f"{team_label(team)}: слово {word_state}, "
            f"объясняющий {explainer}, {sent_state}."
        )
    lines.extend(["", f"Босс: {boss}."])
    return "\n".join(lines)
