import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from constants import (
    CB_GS_CANCEL,
    CB_GS_FINISH,
    CB_GS_JOIN_PREFIX,
    CB_GS_NEW_PREFIX,
    CB_GS_NEXT,
    CB_GS_SCORE,
    CB_GS_SKIP,
    CB_GS_START,
    CB_GS_WORD,
    TEAM_NAMES,
)
from database import DatabaseError, SQLiteHistoryStorage
from handlers.ui import edit_menu
from keyboards import (
    create_play_games_keyboard,
    create_session_lobby_keyboard,
    create_session_play_keyboard,
)
from services.random_generator import WordGame

logger = logging.getLogger(__name__)


@dataclass
class GroupSession:
    """состояние групповой сессии в чате"""

    game: WordGame
    host_id: int
    current_team: int
    started: bool
    explainer_id: int | None
    players: dict[int, str] = field(default_factory=dict)
    team_of: dict[int, int] = field(default_factory=dict)
    scores: list[int] = field(default_factory=lambda: [0] * len(TEAM_NAMES))
    issued: set[str] = field(default_factory=set)


def create_group_session_router(
    word_games: list[WordGame],
    storage: SQLiteHistoryStorage,
) -> Router:
    """создаёт роутер групповых сессий (команды + табло, слова в ЛС)"""
    router = Router()
    games_by_id = {game.game_id: game for game in word_games}
    sessions: dict[int, GroupSession] = {}

    @router.message(Command("play"))
    async def handle_play(message: Message) -> None:
        """показывает выбор игры для групповой сессии"""
        if not _is_group(message.chat.type):
            await message.answer("Групповые сессии работают в групповом чате.")
            return
        await message.answer(
            "Выбери игру для сессии:",
            reply_markup=create_play_games_keyboard(word_games),
        )

    @router.callback_query(_startswith(CB_GS_NEW_PREFIX))
    async def handle_new(callback: CallbackQuery) -> None:
        """создаёт сессию и показывает лобби"""
        message = callback.message
        if not isinstance(message, Message):
            await callback.answer()
            return
        game = games_by_id.get((callback.data or "")[len(CB_GS_NEW_PREFIX) :])
        if game is None:
            await callback.answer()
            return

        sessions[message.chat.id] = GroupSession(
            game=game,
            host_id=callback.from_user.id,
            current_team=0,
            started=False,
            explainer_id=None,
        )
        await edit_menu(
            callback,
            _render_lobby(sessions[message.chat.id]),
            create_session_lobby_keyboard(),
        )

    @router.callback_query(_startswith(CB_GS_JOIN_PREFIX))
    async def handle_join(callback: CallbackQuery) -> None:
        """добавляет игрока в команду в лобби"""
        session, _ = _lookup(callback, sessions)
        if session is None or session.started:
            await callback.answer()
            return
        team = _parse_team((callback.data or "")[len(CB_GS_JOIN_PREFIX) :])
        if team is None:
            await callback.answer()
            return

        user = callback.from_user
        session.players[user.id] = user.full_name
        session.team_of[user.id] = team
        await edit_menu(
            callback, _render_lobby(session), create_session_lobby_keyboard()
        )

    @router.callback_query(F.data == CB_GS_START)
    async def handle_start(callback: CallbackQuery) -> None:
        """запускает сессию (только создатель)"""
        session, _ = _lookup(callback, sessions)
        if session is None:
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Начать может создатель.", show_alert=True)
            return
        if len(session.players) == 0:
            await callback.answer("Нет игроков.", show_alert=True)
            return

        session.started = True
        await edit_menu(
            callback, _render_play(session), create_session_play_keyboard()
        )

    @router.callback_query(F.data == CB_GS_CANCEL)
    async def handle_cancel(callback: CallbackQuery) -> None:
        """отменяет сессию (только создатель)"""
        session, chat_id = _lookup(callback, sessions)
        if session is None or chat_id is None:
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Отменить может создатель.", show_alert=True)
            return

        sessions.pop(chat_id, None)
        await _edit_final(callback, "Сессия отменена.")

    @router.callback_query(F.data == CB_GS_WORD)
    async def handle_word(callback: CallbackQuery) -> None:
        """выдаёт слово объясняющему в ЛС"""
        session, _ = _lookup(callback, sessions)
        if session is None or not session.started:
            await callback.answer()
            return
        user = callback.from_user
        if session.team_of.get(user.id) != session.current_team:
            await callback.answer(
                "Слово берёт игрок команды, чей ход.", show_alert=True
            )
            return

        delivered = await _deliver_word(callback, session, storage, user.id)
        if not delivered:
            return
        session.explainer_id = user.id
        await edit_menu(
            callback, _render_play(session), create_session_play_keyboard()
        )

    @router.callback_query(F.data == CB_GS_SCORE)
    async def handle_score(callback: CallbackQuery) -> None:
        """начисляет очко текущей команде и выдаёт следующее слово"""
        session, _ = _lookup(callback, sessions)
        if session is None or not session.started:
            await callback.answer()
            return
        if callback.from_user.id not in session.team_of:
            await callback.answer("Только участники сессии.", show_alert=True)
            return

        session.scores[session.current_team] += 1
        if session.explainer_id is not None:
            delivered = await _deliver_word(
                callback, session, storage, session.explainer_id
            )
            if not delivered:
                return
        await edit_menu(
            callback, _render_play(session), create_session_play_keyboard()
        )

    @router.callback_query(F.data == CB_GS_SKIP)
    async def handle_skip(callback: CallbackQuery) -> None:
        """выдаёт следующее слово без начисления очка"""
        session, _ = _lookup(callback, sessions)
        if session is None or not session.started:
            await callback.answer()
            return
        if session.explainer_id is None:
            await callback.answer("Сначала возьми слово.", show_alert=True)
            return

        delivered = await _deliver_word(
            callback, session, storage, session.explainer_id
        )
        if not delivered:
            return
        await edit_menu(
            callback, _render_play(session), create_session_play_keyboard()
        )

    @router.callback_query(F.data == CB_GS_NEXT)
    async def handle_next(callback: CallbackQuery) -> None:
        """передаёт ход следующей команде"""
        session, _ = _lookup(callback, sessions)
        if session is None or not session.started:
            await callback.answer()
            return
        if callback.from_user.id not in session.team_of:
            await callback.answer("Только участники сессии.", show_alert=True)
            return

        session.current_team = (session.current_team + 1) % len(TEAM_NAMES)
        session.explainer_id = None
        await edit_menu(
            callback, _render_play(session), create_session_play_keyboard()
        )

    @router.callback_query(F.data == CB_GS_FINISH)
    async def handle_finish(callback: CallbackQuery) -> None:
        """завершает сессию и показывает итоговое табло"""
        session, chat_id = _lookup(callback, sessions)
        if session is None or chat_id is None:
            await callback.answer()
            return
        if callback.from_user.id not in session.team_of:
            await callback.answer("Только участники сессии.", show_alert=True)
            return

        sessions.pop(chat_id, None)
        await _edit_final(callback, "Игра окончена.\n\n" + _render_scores(session))

    return router


async def _deliver_word(
    callback: CallbackQuery,
    session: GroupSession,
    storage: SQLiteHistoryStorage,
    explainer_id: int,
) -> bool:
    """выбирает слово и шлёт его объясняющему в ЛС, возвращает успех"""
    try:
        custom = await storage.get_custom_words(session.game.game_id)
    except DatabaseError:
        logger.exception("database_error", extra={"action": "gs_word"})
        await callback.answer("Ошибка БД. Попробуйте позже.", show_alert=True)
        return False

    pool = list(dict.fromkeys(session.game.words + custom))
    word, _ = _pick_word(pool, session.issued)

    bot = callback.bot
    if bot is None:
        await callback.answer()
        return False
    try:
        await bot.send_message(explainer_id, f"Слово: {word}")
    except TelegramForbiddenError:
        await callback.answer(
            "Объясняющий не открыл ЛС с ботом (нужен /start в личке).",
            show_alert=True,
        )
        return False
    return True


async def _edit_final(callback: CallbackQuery, text: str) -> None:
    """заменяет сообщение сессии финальным текстом без клавиатуры"""
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text)
        except TelegramBadRequest:
            pass
    await callback.answer()


def _startswith(prefix: str) -> Callable[[CallbackQuery], bool]:
    """фильтр callback по префиксу данных"""
    return lambda callback: (
        callback.data is not None and callback.data.startswith(prefix)
    )


def _lookup(
    callback: CallbackQuery, sessions: dict[int, GroupSession]
) -> tuple[GroupSession | None, int | None]:
    """находит сессию по чату callback"""
    message = callback.message
    if not isinstance(message, Message):
        return None, None
    chat_id = message.chat.id
    return sessions.get(chat_id), chat_id


def _parse_team(value: str) -> int | None:
    """разбирает индекс команды из callback-данных"""
    try:
        team = int(value)
    except ValueError:
        return None
    if team < 0 or team >= len(TEAM_NAMES):
        return None
    return team


def _is_group(chat_type: str) -> bool:
    """проверяет что чат групповой"""
    return chat_type in ("group", "supergroup")


def _pick_word(pool: list[str], issued: set[str]) -> tuple[str, bool]:
    """выбирает слово без повтора в сессии, сбрасывая круг при исчерпании"""
    available = [word for word in pool if word not in issued]
    reset = False
    if len(available) == 0:
        issued.clear()
        available = list(pool)
        reset = True
    word = random.choice(available)
    issued.add(word)
    return word, reset


def _render_lobby(session: GroupSession) -> str:
    """отображает лобби с составами команд"""
    lines = [f"Сессия «{session.game.title}». Выбери команду:", ""]
    for index, name in enumerate(TEAM_NAMES):
        members = [
            player
            for user_id, player in session.players.items()
            if session.team_of.get(user_id) == index
        ]
        lines.append(f"{name}: {', '.join(members) if members else '-'}")
    return "\n".join(lines)


def _render_play(session: GroupSession) -> str:
    """отображает табло и текущий ход"""
    lines = [f"Игра: {session.game.title}", ""]
    for index, name in enumerate(TEAM_NAMES):
        marker = "  <- ход" if index == session.current_team else ""
        lines.append(f"{name}: {session.scores[index]}{marker}")
    if session.explainer_id is not None:
        explainer = session.players.get(session.explainer_id, "игрок")
        lines.extend(["", f"Объясняет: {explainer}"])
    return "\n".join(lines)


def _render_scores(session: GroupSession) -> str:
    """отображает итоговое табло с победителем"""
    lines = []
    for index, name in enumerate(TEAM_NAMES):
        lines.append(f"{name}: {session.scores[index]}")
    best = max(session.scores)
    winners = [
        TEAM_NAMES[index]
        for index, score in enumerate(session.scores)
        if score == best
    ]
    if len(winners) == 1:
        lines.append(f"\nПобедитель: {winners[0]}")
    else:
        lines.append("\nНичья")
    return "\n".join(lines)
