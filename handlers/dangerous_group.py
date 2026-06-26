import logging
import random
from dataclasses import dataclass, field

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, Message

from constants import (
    CB_DG_BOSS,
    CB_DG_CURSE,
    CB_DG_EXPLAIN,
    CB_DG_FINISH,
    CB_DG_NEXT,
    CB_DG_OPEN,
    CB_DG_WORD,
    DANGEROUS_WORDS_GAME_ID,
    team_label,
)
from database import DatabaseError, SQLiteHistoryStorage
from keyboards import create_dangerous_group_keyboard
from services.random_generator import Curse, DangerousWordsContent

logger = logging.getLogger(__name__)


@dataclass
class DangerousGroup:
    """состояние командной партии «опасные слова» в чате"""

    host_id: int
    explaining_team: int
    explainer_id: int | None
    explainer_name: str | None
    boss_revealed: bool = False
    issued_words: set[str] = field(default_factory=set)
    issued_curses: set[str] = field(default_factory=set)


def create_dangerous_group_router(
    content: DangerousWordsContent,
    storage: SQLiteHistoryStorage,
) -> Router:
    """создаёт роутер командной игры «опасные слова» (бот-крупье)"""
    router = Router()
    sessions: dict[int, DangerousGroup] = {}

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
        session, _ = _lookup(callback, sessions)
        if session is None:
            await callback.answer()
            return
        session.explainer_id = callback.from_user.id
        session.explainer_name = callback.from_user.full_name
        await _edit_board(callback, session)
        await callback.answer("Ты объясняешь.")

    @router.callback_query(F.data == CB_DG_WORD)
    async def handle_word(callback: CallbackQuery) -> None:
        """шлёт слово в ЛС загадывающему и объясняющему"""
        session, _ = _lookup(callback, sessions)
        if session is None:
            await callback.answer()
            return
        if session.explainer_id is None:
            await callback.answer(
                "Сначала из объясняющей команды нажмите «Я объясняю».",
                show_alert=True,
            )
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

        word = _pick(pool, session.issued_words)
        recipients = {session.explainer_id, callback.from_user.id}
        delivered = True
        for user_id in recipients:
            try:
                await bot.send_message(user_id, f"Слово: {word}")
            except TelegramForbiddenError:
                delivered = False
        if not delivered:
            await callback.answer(
                "Кому-то не дошло: нужен /start в личке с ботом.",
                show_alert=True,
            )
            return
        await callback.answer("Слово ушло в ЛС.")

    @router.callback_query(F.data == CB_DG_NEXT)
    async def handle_next(callback: CallbackQuery) -> None:
        """меняет роли команд (загадывает/объясняет)"""
        session, _ = _lookup(callback, sessions)
        if session is None:
            await callback.answer()
            return
        session.explaining_team = 1 - session.explaining_team
        session.explainer_id = None
        session.explainer_name = None
        await _edit_board(callback, session)
        await callback.answer()

    @router.callback_query(F.data == CB_DG_CURSE)
    async def handle_curse(callback: CallbackQuery) -> None:
        """тянет проклятие и публикует его в чат (только ведущий)"""
        session, _ = _lookup(callback, sessions)
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

        curse = _pick_curse(pool, session.issued_curses)
        if curse is None:
            await callback.answer("Проклятий нет.", show_alert=True)
            return
        await message.answer(f"Проклятие: {curse.title}\n{curse.description}")
        await callback.answer()

    @router.callback_query(F.data == CB_DG_BOSS)
    async def handle_boss(callback: CallbackQuery) -> None:
        """раскрывает единственного босса сессии (только ведущий)"""
        session, _ = _lookup(callback, sessions)
        message = callback.message
        if session is None or not isinstance(message, Message):
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer(
                "Босса раскрывает ведущий.", show_alert=True
            )
            return
        if session.boss_revealed:
            await callback.answer(
                "Босс уже раскрыт - он один на игру.", show_alert=True
            )
            return
        try:
            pool = content.bosses + await storage.get_custom_bosses()
        except DatabaseError:
            logger.exception("database_error", extra={"action": "dg_boss"})
            await callback.answer("Ошибка БД. Попробуйте позже.", show_alert=True)
            return

        boss = random.choice(pool)
        session.boss_revealed = True
        await message.answer(f"Босс (финал): {boss.name}\n{boss.description}")
        await _edit_board(callback, session)
        await callback.answer()

    @router.callback_query(F.data == CB_DG_FINISH)
    async def handle_finish(callback: CallbackQuery) -> None:
        """завершает партию (только ведущий)"""
        session, chat_id = _lookup(callback, sessions)
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


def _lookup(
    callback: CallbackQuery, sessions: dict[int, DangerousGroup]
) -> tuple[DangerousGroup | None, int | None]:
    """находит сессию по чату callback"""
    message = callback.message
    if not isinstance(message, Message):
        return None, None
    chat_id = message.chat.id
    return sessions.get(chat_id), chat_id


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


def _pick(pool: list[str], issued: set[str]) -> str:
    """выбирает слово без повтора в сессии, сбрасывая круг при исчерпании"""
    available = [word for word in pool if word not in issued]
    if len(available) == 0:
        issued.clear()
        available = list(pool)
    word = random.choice(available)
    issued.add(word)
    return word


def _pick_curse(pool: list[Curse], issued: set[str]) -> Curse | None:
    """выбирает проклятие без повтора в сессии"""
    if len(pool) == 0:
        return None
    available = [curse for curse in pool if curse.id not in issued]
    if len(available) == 0:
        issued.clear()
        available = list(pool)
    curse = random.choice(available)
    issued.add(curse.id)
    return curse


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
            f"{team_label(drawing)} жмёт «Тянуть слово» (придёт ей и "
            "объясняющему в ЛС) и пишет запретные слова.",
            f"Объясняющий из {team_label(explaining)} объясняет, не называя "
            "запретных слов.",
            f"Босс: {boss}.",
        ]
    )
