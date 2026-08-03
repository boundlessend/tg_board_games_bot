import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from constants import (
    CB_GS_CANCEL,
    CB_GS_FINISH,
    CB_GS_FINISH_NO,
    CB_GS_FINISH_YES,
    CB_GS_JOIN_PREFIX,
    CB_GS_NEW_PREFIX,
    CB_GS_NEXT,
    CB_GS_REROLL,
    CB_GS_SCORE,
    CB_GS_SKIP,
    CB_GS_START,
    CB_GS_TEAMS_PREFIX,
    CB_GS_TIMER_PREFIX,
    CB_GS_WORD,
    DEFAULT_TURN_SECONDS,
    MAX_TEAMS,
    MIN_TEAMS,
    TURN_SECONDS_OPTIONS,
    team_label,
)
from database import DatabaseError, SQLiteHistoryStorage
from handlers.common import (
    ChatLocks,
    data_startswith,
    is_chat_manager,
    lookup_chat_session,
    make_chat_lock_middleware,
    make_chat_persist_middleware,
    send_with_retry,
)
from handlers.ui import edit_menu
from keyboards import (
    create_play_games_keyboard,
    create_session_finish_keyboard,
    create_session_lobby_keyboard,
    create_session_play_keyboard,
)
from services.content import WordGame, group_games
from services.picking import pick_word

logger = logging.getLogger(__name__)

_SCOPE = "group"


@dataclass
class GroupSession:
    """состояние групповой сессии в чате"""

    game: WordGame
    host_id: int
    team_count: int
    turn_seconds: int
    current_team: int
    started: bool
    explainer_id: int | None
    players: dict[int, str] = field(default_factory=dict)
    team_of: dict[int, int] = field(default_factory=dict)
    scores: list[int] = field(default_factory=list)
    issued: set[str] = field(default_factory=set)
    timer_task: asyncio.Task[None] | None = field(default=None)
    turn_epoch: int = field(default=0)


def create_group_session_router(
    word_games: list[WordGame],
    storage: SQLiteHistoryStorage,
    sessions: dict[int, GroupSession],
    bot_username: str,
) -> Router:
    """создаёт роутер групповых сессий (команды + табло, слова в ЛС)"""
    router = Router()
    playable_games = group_games(word_games)
    games_by_id = {game.game_id: game for game in playable_games}
    locks = ChatLocks()

    async def _persist_chat(chat_id: int) -> None:
        await _persist_chat_session(storage, sessions, chat_id)

    router.callback_query.middleware(make_chat_lock_middleware(locks))
    router.callback_query.middleware(
        make_chat_persist_middleware(_persist_chat, _SCOPE)
    )

    @router.message(Command("play"))
    async def handle_play(message: Message) -> None:
        """показывает выбор игры для групповой сессии"""
        if not _is_group(message.chat.type):
            await message.answer("Групповые сессии работают в групповом чате.")
            return
        await message.answer(
            "Выбери игру для сессии:",
            reply_markup=create_play_games_keyboard(playable_games),
        )

    @router.callback_query(data_startswith(CB_GS_NEW_PREFIX))
    async def handle_new(callback: CallbackQuery) -> None:
        """создаёт сессию и показывает лобби"""
        message = callback.message
        if not isinstance(message, Message):
            await callback.answer()
            return
        if not _is_group(message.chat.type):
            await callback.answer("Командные игры работают в беседе.", show_alert=True)
            return
        game = games_by_id.get((callback.data or "")[len(CB_GS_NEW_PREFIX) :])
        if game is None:
            await callback.answer()
            return
        existing = sessions.get(message.chat.id)
        if existing is not None and existing.started:
            await callback.answer(
                "Партия уже идёт. Сначала «Завершить» или «Отмена».",
                show_alert=True,
            )
            return
        if existing is not None:
            _cancel_timer(existing)

        session = GroupSession(
            game=game,
            host_id=callback.from_user.id,
            team_count=MIN_TEAMS,
            turn_seconds=DEFAULT_TURN_SECONDS,
            current_team=0,
            started=False,
            explainer_id=None,
        )
        sessions[message.chat.id] = session
        await edit_menu(
            callback,
            _render_lobby(session),
            create_session_lobby_keyboard(
                session.team_count, session.turn_seconds, bot_username
            ),
        )

    @router.callback_query(data_startswith(CB_GS_JOIN_PREFIX))
    async def handle_join(callback: CallbackQuery) -> None:
        """добавляет игрока в команду в лобби"""
        session, _ = lookup_chat_session(callback, sessions)
        if session is None or session.started:
            await callback.answer()
            return
        team = _parse_team(
            (callback.data or "")[len(CB_GS_JOIN_PREFIX) :], session.team_count
        )
        if team is None:
            await callback.answer()
            return

        user = callback.from_user
        session.players[user.id] = user.full_name
        session.team_of[user.id] = team
        await edit_menu(
            callback,
            _render_lobby(session),
            create_session_lobby_keyboard(
                session.team_count, session.turn_seconds, bot_username
            ),
        )

    @router.callback_query(data_startswith(CB_GS_TEAMS_PREFIX))
    async def handle_set_teams(callback: CallbackQuery) -> None:
        """меняет число команд в лобби (только создатель)"""
        session, _ = lookup_chat_session(callback, sessions)
        if session is None or session.started:
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Число команд меняет создатель.", show_alert=True)
            return
        count = _parse_count((callback.data or "")[len(CB_GS_TEAMS_PREFIX) :])
        if count is None:
            await callback.answer()
            return

        orphans = [
            user_id for user_id, team in session.team_of.items() if team >= count
        ]
        for user_id in orphans:
            session.team_of.pop(user_id, None)
            session.players.pop(user_id, None)
        session.team_count = count
        await edit_menu(
            callback,
            _render_lobby(session),
            create_session_lobby_keyboard(
                session.team_count, session.turn_seconds, bot_username
            ),
        )

    @router.callback_query(data_startswith(CB_GS_TIMER_PREFIX))
    async def handle_set_timer(callback: CallbackQuery) -> None:
        """меняет время хода в лобби (только создатель)"""
        session, _ = lookup_chat_session(callback, sessions)
        if session is None or session.started:
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Время хода меняет создатель.", show_alert=True)
            return
        seconds = _parse_seconds((callback.data or "")[len(CB_GS_TIMER_PREFIX) :])
        if seconds is None:
            await callback.answer()
            return

        session.turn_seconds = seconds
        await edit_menu(
            callback,
            _render_lobby(session),
            create_session_lobby_keyboard(
                session.team_count, session.turn_seconds, bot_username
            ),
        )

    @router.callback_query(F.data == CB_GS_START)
    async def handle_start(callback: CallbackQuery) -> None:
        """запускает сессию (только создатель)"""
        session, _ = lookup_chat_session(callback, sessions)
        if session is None:
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Начать может создатель.", show_alert=True)
            return
        manned = set(session.team_of.values())
        empty = [
            team_label(index)
            for index in range(session.team_count)
            if index not in manned
        ]
        if empty:
            await callback.answer(
                "Без игроков: " + ", ".join(empty) + ". Наберите людей "
                "или уменьшите число команд.",
                show_alert=True,
            )
            return

        session.started = True
        session.scores = [0] * session.team_count
        await edit_menu(callback, _render_play(session), create_session_play_keyboard())

    @router.callback_query(F.data == CB_GS_CANCEL)
    async def handle_cancel(callback: CallbackQuery) -> None:
        """отменяет сессию (создатель или администратор чата)"""
        session, chat_id = lookup_chat_session(callback, sessions)
        if session is None or chat_id is None:
            await callback.answer()
            return
        if not await _may_manage(callback, session, chat_id):
            await callback.answer(
                "Отменить может создатель или админ чата.", show_alert=True
            )
            return

        _cancel_timer(session)
        sessions.pop(chat_id, None)
        await _edit_final(callback, "Сессия отменена.")

    @router.callback_query(F.data == CB_GS_WORD)
    async def handle_word(callback: CallbackQuery) -> None:
        """выдаёт слово объясняющему в ЛС и запускает таймер хода"""
        session, chat_id = lookup_chat_session(callback, sessions)
        if session is None or chat_id is None or not session.started:
            await callback.answer()
            return
        user = callback.from_user
        if session.team_of.get(user.id) != session.current_team:
            await callback.answer(
                "Слово берёт игрок команды, чей ход.", show_alert=True
            )
            return

        previous_explainer = session.explainer_id
        delivered = await _deliver_word(callback, session, storage, user.id)
        if not delivered:
            return
        session.explainer_id = user.id
        bot = callback.bot
        if bot is not None:
            _start_timer(chat_id, session, bot, locks, _persist_chat)
            await _announce_explainer_change(
                bot, chat_id, session, previous_explainer, user.id
            )
        await edit_menu(callback, _render_play(session), create_session_play_keyboard())

    @router.callback_query(F.data == CB_GS_SCORE)
    async def handle_score(callback: CallbackQuery) -> None:
        """начисляет очко; следующее слово берут вручную «Слово в ЛС»"""
        session, _ = lookup_chat_session(callback, sessions)
        if session is None or not session.started:
            await callback.answer()
            return
        if callback.from_user.id not in session.team_of:
            await callback.answer("Только участники сессии.", show_alert=True)
            return
        if session.explainer_id is None:
            await callback.answer("Сначала возьми слово.", show_alert=True)
            return

        session.scores[session.current_team] += 1
        # слово отыграно: следующее берут кнопкой «Слово в ЛС», не авто
        session.explainer_id = None
        await edit_menu(callback, _render_play(session), create_session_play_keyboard())

    @router.callback_query(F.data == CB_GS_SKIP)
    async def handle_skip(callback: CallbackQuery) -> None:
        """выдаёт следующее слово без начисления очка"""
        session, _ = lookup_chat_session(callback, sessions)
        if session is None or not session.started:
            await callback.answer()
            return
        if session.team_of.get(callback.from_user.id) != session.current_team:
            await callback.answer(
                "Меняет слово игрок команды, чей ход.", show_alert=True
            )
            return
        if session.explainer_id is None:
            await callback.answer("Сначала возьми слово.", show_alert=True)
            return

        delivered = await _deliver_word(
            callback, session, storage, session.explainer_id
        )
        if not delivered:
            return
        await edit_menu(callback, _render_play(session), create_session_play_keyboard())

    @router.callback_query(F.data == CB_GS_REROLL)
    async def handle_reroll(callback: CallbackQuery) -> None:
        """меняет слово со штрафом -1 очка текущей команде"""
        session, _ = lookup_chat_session(callback, sessions)
        if session is None or not session.started:
            await callback.answer()
            return
        if session.team_of.get(callback.from_user.id) != session.current_team:
            await callback.answer(
                "Реролл делает игрок команды, чей ход.", show_alert=True
            )
            return
        if session.explainer_id is None:
            await callback.answer("Сначала возьми слово.", show_alert=True)
            return

        delivered = await _deliver_word(
            callback, session, storage, session.explainer_id
        )
        if not delivered:
            return
        session.scores[session.current_team] -= 1
        await edit_menu(callback, _render_play(session), create_session_play_keyboard())

    @router.callback_query(F.data == CB_GS_NEXT)
    async def handle_next(callback: CallbackQuery) -> None:
        """передаёт ход следующей команде"""
        session, _ = lookup_chat_session(callback, sessions)
        if session is None or not session.started:
            await callback.answer()
            return
        if callback.from_user.id not in session.team_of:
            await callback.answer("Только участники сессии.", show_alert=True)
            return

        _cancel_timer(session)
        session.current_team = (session.current_team + 1) % session.team_count
        session.explainer_id = None
        await edit_menu(callback, _render_play(session), create_session_play_keyboard())

    @router.callback_query(F.data == CB_GS_FINISH)
    async def handle_finish_request(callback: CallbackQuery) -> None:
        """спрашивает подтверждение перед завершением партии"""
        session, _ = lookup_chat_session(callback, sessions)
        if session is None or not session.started:
            await callback.answer()
            return
        if callback.from_user.id not in session.team_of:
            await callback.answer("Только участники сессии.", show_alert=True)
            return

        await edit_menu(
            callback,
            _render_play(session) + "\n\nЗавершить партию и показать итоги?",
            create_session_finish_keyboard(),
        )

    @router.callback_query(F.data == CB_GS_FINISH_NO)
    async def handle_finish_cancel(callback: CallbackQuery) -> None:
        """возвращает игру после отказа от завершения"""
        session, _ = lookup_chat_session(callback, sessions)
        if session is None or not session.started:
            await callback.answer()
            return
        await edit_menu(callback, _render_play(session), create_session_play_keyboard())

    @router.callback_query(F.data == CB_GS_FINISH_YES)
    async def handle_finish(callback: CallbackQuery) -> None:
        """завершает сессию и показывает итоговое табло"""
        session, chat_id = lookup_chat_session(callback, sessions)
        if session is None or chat_id is None or not session.started:
            await callback.answer()
            return
        if callback.from_user.id not in session.team_of:
            await callback.answer("Только участники сессии.", show_alert=True)
            return

        _cancel_timer(session)
        sessions.pop(chat_id, None)
        await _edit_final(callback, "Игра окончена.\n\n" + _render_scores(session))

    async def _may_manage(
        callback: CallbackQuery, session: GroupSession, chat_id: int
    ) -> bool:
        """проверяет право распоряжаться партией"""
        bot = callback.bot
        if bot is None:
            return callback.from_user.id == session.host_id
        return await is_chat_manager(
            bot, chat_id, callback.from_user.id, session.host_id
        )

    return router


def _dump_session(session: GroupSession) -> dict[str, Any]:
    """сериализует групповую сессию в словарь (таймер не сохраняется)"""
    return {
        "game_id": session.game.game_id,
        "host_id": session.host_id,
        "team_count": session.team_count,
        "turn_seconds": session.turn_seconds,
        "current_team": session.current_team,
        "started": session.started,
        "explainer_id": session.explainer_id,
        "players": session.players,
        "team_of": session.team_of,
        "scores": session.scores,
        "issued": list(session.issued),
    }


def _load_session(
    data: dict[str, Any], games_by_id: dict[str, WordGame]
) -> GroupSession | None:
    """восстанавливает сессию из словаря, None если игра пропала из реестра"""
    game = games_by_id.get(data["game_id"])
    if game is None:
        return None
    # ponytail: таймер хода после рестарта не возрождаем - ход доигрывают вручную
    return GroupSession(
        game=game,
        host_id=data["host_id"],
        team_count=data["team_count"],
        turn_seconds=data["turn_seconds"],
        current_team=data["current_team"],
        started=data["started"],
        explainer_id=data["explainer_id"],
        players={int(key): name for key, name in data["players"].items()},
        team_of={int(key): team for key, team in data["team_of"].items()},
        scores=list(data["scores"]),
        issued=set(data["issued"]),
    )


async def _persist_chat_session(
    storage: SQLiteHistoryStorage,
    sessions: dict[int, GroupSession],
    chat_id: int,
) -> None:
    """сохраняет или удаляет снапшот сессии одного чата"""
    session = sessions.get(chat_id)
    if session is None:
        await storage.delete_session(_SCOPE, str(chat_id))
        return
    await storage.save_session(_SCOPE, str(chat_id), json.dumps(_dump_session(session)))


async def restore_group_sessions(
    storage: SQLiteHistoryStorage,
    word_games: list[WordGame],
    sessions: dict[int, GroupSession],
) -> None:
    """наполняет словарь сессий снапшотами из хранилища при старте"""
    games_by_id = {game.game_id: game for game in word_games}
    raw = await storage.load_session_scope(_SCOPE)
    for key, data in raw.items():
        try:
            session = _load_session(json.loads(data), games_by_id)
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            # снапшот несовместимой/повреждённой схемы - пропускаем
            logger.exception(
                "session_restore_failed",
                extra={"scope": _SCOPE, "key": key},
            )
            continue
        if session is not None:
            sessions[int(key)] = session


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
    word = pick_word(pool, session.issued)

    bot = callback.bot
    if bot is None:
        session.issued.discard(word)
        await callback.answer()
        return False
    try:
        await bot.send_message(explainer_id, f"Слово: {word}")
    except TelegramForbiddenError:
        session.issued.discard(word)
        await callback.answer(
            "Объясняющий не открыл ЛС с ботом (нужен /start в личке).",
            show_alert=True,
        )
        return False
    return True


async def _announce_explainer_change(
    bot: Bot,
    chat_id: int,
    session: GroupSession,
    previous_explainer: int | None,
    new_explainer: int,
) -> None:
    """предупреждает чат, что слово перехватил другой игрок команды"""
    if previous_explainer is None or previous_explainer == new_explainer:
        return
    previous_name = session.players.get(previous_explainer, "игрок")
    new_name = session.players.get(new_explainer, "игрок")
    await send_with_retry(
        bot,
        chat_id,
        f"Слово перешло: объясняет {new_name} вместо {previous_name}.",
    )


async def _edit_final(callback: CallbackQuery, text: str) -> None:
    """заменяет сообщение сессии финальным текстом без клавиатуры"""
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text)
        except TelegramBadRequest:
            pass
    await callback.answer()


def _parse_team(value: str, team_count: int) -> int | None:
    """разбирает индекс команды из callback-данных"""
    try:
        team = int(value)
    except ValueError:
        return None
    if team < 0 or team >= team_count:
        return None
    return team


def _parse_count(value: str) -> int | None:
    """разбирает число команд из callback-данных"""
    try:
        count = int(value)
    except ValueError:
        return None
    if count < MIN_TEAMS or count > MAX_TEAMS:
        return None
    return count


def _parse_seconds(value: str) -> int | None:
    """разбирает время хода из callback-данных"""
    try:
        seconds = int(value)
    except ValueError:
        return None
    if seconds not in TURN_SECONDS_OPTIONS:
        return None
    return seconds


def _start_timer(
    chat_id: int,
    session: GroupSession,
    bot: Bot,
    locks: ChatLocks,
    persist: Callable[[int], Awaitable[None]],
) -> None:
    """запускает таймер хода, если он включён и ещё не идёт"""
    if session.turn_seconds <= 0 or session.timer_task is not None:
        return
    session.timer_task = asyncio.create_task(
        _run_timer(chat_id, session, bot, locks, persist, session.turn_epoch)
    )


def _cancel_timer(session: GroupSession) -> None:
    """останавливает текущий таймер хода и обесценивает его эпоху"""
    session.turn_epoch += 1
    if session.timer_task is not None:
        session.timer_task.cancel()
        session.timer_task = None


async def _run_timer(
    chat_id: int,
    session: GroupSession,
    bot: Bot,
    locks: ChatLocks,
    persist: Callable[[int], Awaitable[None]],
    epoch: int,
) -> None:
    """ждёт время хода и передаёт ход следующей команде по истечении"""
    try:
        await asyncio.sleep(session.turn_seconds)
    except asyncio.CancelledError:
        return
    async with locks.hold(chat_id):
        # ponytail: ход уже сменён вручную/новым таймером, если эпоха ушла
        if session.turn_epoch != epoch:
            return
        session.turn_epoch += 1
        session.timer_task = None
        session.current_team = (session.current_team + 1) % session.team_count
        session.explainer_id = None
        try:
            await persist(chat_id)
        except DatabaseError:
            logger.exception("session_persist_failed", extra={"scope": _SCOPE})
        await send_with_retry(
            bot,
            chat_id,
            "Время вышло! Ход переходит.\n\n" + _render_play(session),
            create_session_play_keyboard(),
        )


def _is_group(chat_type: str) -> bool:
    """проверяет что чат групповой"""
    return chat_type in ("group", "supergroup")


def _render_lobby(session: GroupSession) -> str:
    """отображает лобби с составами команд"""
    lines = [
        f"Сессия «{session.game.title}». Команд: {session.team_count}.",
        "Выбери команду (число сверху - сколько команд):",
        "",
    ]
    for index in range(session.team_count):
        members = [
            player
            for user_id, player in session.players.items()
            if session.team_of.get(user_id) == index
        ]
        lines.append(f"{team_label(index)}: {', '.join(members) if members else '-'}")
    return "\n".join(lines)


def _render_play(session: GroupSession) -> str:
    """отображает табло и текущий ход"""
    lines = [f"Игра: {session.game.title}", ""]
    for index in range(session.team_count):
        marker = "  <- ход" if index == session.current_team else ""
        lines.append(f"{team_label(index)}: {session.scores[index]}{marker}")
    if session.explainer_id is not None:
        explainer = session.players.get(session.explainer_id, "игрок")
        lines.extend(["", f"Объясняет: {explainer}"])
    return "\n".join(lines)


def _render_scores(session: GroupSession) -> str:
    """отображает итоговое табло с победителем"""
    lines = []
    for index in range(session.team_count):
        lines.append(f"{team_label(index)}: {session.scores[index]}")
    best = max(session.scores)
    winners = [
        team_label(index) for index, score in enumerate(session.scores) if score == best
    ]
    if len(winners) == 1:
        lines.append(f"\nПобедитель: {winners[0]}")
    else:
        lines.append("\nНичья")
    return "\n".join(lines)
