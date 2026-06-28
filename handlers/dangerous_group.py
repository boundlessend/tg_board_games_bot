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
    CB_DG_EXPLAIN,
    CB_DG_FINISH,
    CB_DG_NEXT,
    CB_DG_OPEN,
    CB_DG_SEND,
    CB_DG_WORD,
    DANGEROUS_WORDS_GAME_ID,
    team_label,
)
from database import DatabaseError, SQLiteHistoryStorage
from handlers.common import (
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
    """состояние командной партии «опасные слова» в чате"""

    host_id: int
    explaining_team: int
    explainer_id: int | None
    explainer_name: str | None
    current_word: str | None = None
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
        session = DangerousGroup(
            host_id=callback.from_user.id,
            explaining_team=0,
            explainer_id=None,
            explainer_name=None,
        )
        sessions[message.chat.id] = session
        await message.answer(
            _render_board(session),
            reply_markup=create_dangerous_group_keyboard(),
        )
        await callback.answer()

    @router.callback_query(F.data == CB_DG_EXPLAIN)
    async def handle_explain(callback: CallbackQuery) -> None:
        """назначает объясняющего из объясняющей команды"""
        session, _ = lookup_chat_session(callback, sessions)
        if session is None:
            await callback.answer()
            return
        session.explainer_id = callback.from_user.id
        session.explainer_name = callback.from_user.full_name
        await _edit_board(callback, session)
        await callback.answer("Ты объясняешь.")

    @router.callback_query(F.data == CB_DG_WORD)
    async def handle_word(callback: CallbackQuery) -> None:
        """тянет слово в ЛС загадывающей команде, чтобы написать запретные"""
        session, _ = lookup_chat_session(callback, sessions)
        if session is None:
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
        session.current_word = word
        try:
            await bot.send_message(callback.from_user.id, f"Слово: {word}")
        except TelegramForbiddenError:
            await callback.answer(
                "Не дошло: нужен /start в личке с ботом.", show_alert=True
            )
            return
        await callback.answer(
            "Слово у тебя в ЛС: покажи команде, напиши запретные."
        )

    @router.callback_query(F.data == CB_DG_SEND)
    async def handle_send(callback: CallbackQuery) -> None:
        """отправляет вытянутое слово объясняющему из другой команды"""
        session, _ = lookup_chat_session(callback, sessions)
        if session is None:
            await callback.answer()
            return
        if session.current_word is None:
            await callback.answer("Сначала вытяните слово.", show_alert=True)
            return
        if session.explainer_id is None:
            await callback.answer(
                "Из объясняющей команды нажмите «Я объясняю».",
                show_alert=True,
            )
            return
        bot = callback.bot
        if bot is None:
            await callback.answer()
            return
        try:
            await bot.send_message(
                session.explainer_id, f"Слово: {session.current_word}"
            )
        except TelegramForbiddenError:
            await callback.answer(
                "Объясняющему не дошло: нужен /start в личке с ботом.",
                show_alert=True,
            )
            return
        await callback.answer("Слово ушло объясняющему в ЛС.")

    @router.callback_query(F.data == CB_DG_NEXT)
    async def handle_next(callback: CallbackQuery) -> None:
        """меняет роли команд (загадывает/объясняет) - только ведущий"""
        session, _ = lookup_chat_session(callback, sessions)
        if session is None:
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Ход передаёт ведущий.", show_alert=True)
            return
        session.explaining_team = 1 - session.explaining_team
        session.explainer_id = None
        session.explainer_name = None
        session.current_word = None
        await _edit_board(callback, session)
        await callback.answer()

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
        "explaining_team": session.explaining_team,
        "explainer_id": session.explainer_id,
        "explainer_name": session.explainer_name,
        "current_word": session.current_word,
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
        explaining_team=data["explaining_team"],
        explainer_id=data["explainer_id"],
        explainer_name=data["explainer_name"],
        current_word=data["current_word"],
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
        sessions[int(key)] = _load_session(json.loads(data))


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


def _curse_text(curse: Curse) -> str:
    """текст сообщения с проклятием"""
    return f"Проклятие: {curse.title}\n{curse.description}"


def _boss_text(boss: Boss) -> str:
    """текст сообщения с боссом"""
    return f"Босс (финал): {boss.name}\n{boss.description}"


def _render_board(session: DangerousGroup) -> str:
    """рисует поле командной партии «опасные слова»"""
    explaining = session.explaining_team
    drawing = 1 - explaining
    explainer = session.explainer_name or "не выбран"
    boss = "раскрыт" if session.boss_revealed else "в колоде (финал)"
    return "\n".join(
        [
            "Опасные слова - 2 команды.",
            f"Загадывает: {team_label(drawing)}. "
            f"Объясняет: {team_label(explaining)}.",
            f"Объясняющий: {explainer}.",
            "",
            f"{team_label(drawing)} жмёт «Тянуть слово» (придёт ей в ЛС), "
            "пишет запретные, затем «Отправить слово».",
            f"Объясняющему из {team_label(explaining)} слово придёт в ЛС: "
            "он объясняет, не называя запретных.",
            f"Босс: {boss}.",
        ]
    )
