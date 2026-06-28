import asyncio
import json
import logging
import random
from dataclasses import asdict, dataclass, field
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)

from constants import (
    BUNKER_CARD_LABELS,
    CB_BK_CANCEL,
    CB_BK_JOIN,
    CB_BK_MODE,
    CB_BK_NEXT,
    CB_BK_OPEN,
    CB_BK_REVEAL,
    CB_BK_SOLO_CANCEL,
    CB_BK_SOLO_START,
    CB_BK_START,
    CB_BK_STORY_NO,
    CB_BK_STORY_TALLY,
    CB_BK_STORY_YES,
    CB_BK_VOTE_PREFIX,
    CB_BK_VOTE_START,
    CB_BK_VOTE_TALLY,
)
from database import SQLiteHistoryStorage
from handlers.common import (
    data_startswith,
    make_chat_lock_middleware,
    make_persist_middleware,
)
from keyboards import (
    create_bunker_lobby_keyboard,
    create_bunker_reveal_keyboard,
    create_bunker_solo_lobby_keyboard,
    create_bunker_story_keyboard,
    create_bunker_vote_keyboard,
)
from services.bunker import (
    MAX_PLAYERS,
    MIN_PLAYERS,
    REVEAL_ORDER,
    BunkerContent,
    PlayerHand,
    RoundsPlan,
    deal_hands,
    pick_catastrophe,
    pick_pairs,
    rounds_plan,
    vote_leaders,
)

logger = logging.getLogger(__name__)

ROUNDS_TOTAL = 5
_SCOPE = "bunker"
_LOBBY_SCOPE = "bunker_lobby"


@dataclass(frozen=True)
class Challenge:
    """испытание финала «история выживания»"""

    group: str
    kind: str
    text: str


@dataclass
class BunkerSession:
    """состояние партии в бункер в одном чате"""

    host_id: int
    board_chat_id: int
    phase: str = "lobby"
    board_message_id: int | None = None
    story_mode: bool = False
    catastrophe: str = ""
    pairs: list[tuple[str, str]] = field(default_factory=list)
    plan: RoundsPlan | None = None
    round_no: int = 0
    votes_pending: int = 0
    revote: bool = False
    players: dict[int, str] = field(default_factory=dict)
    hands: dict[int, PlayerHand] = field(default_factory=dict)
    revealed_count: dict[int, int] = field(default_factory=dict)
    excluded: set[int] = field(default_factory=set)
    votes: dict[int, int] = field(default_factory=dict)
    vote_candidates: list[int] = field(default_factory=list)
    survivors_bunker: list[int] = field(default_factory=list)
    survivors_exiles: list[int] = field(default_factory=list)
    finale_queue: list[Challenge] = field(default_factory=list)
    finale_index: int = 0
    story_votes: dict[int, bool] = field(default_factory=dict)


@dataclass
class SoloLobby:
    """лёгкое лобби режима «отдельно»: раздаёт карты в личку по коду"""

    host_id: int
    code: str
    message_id: int | None = None
    started: bool = False
    members: dict[int, str] = field(default_factory=dict)


def create_bunker_router(
    content: BunkerContent,
    storage: SQLiteHistoryStorage,
    sessions: dict[int, BunkerSession],
    lobbies: dict[str, SoloLobby],
    member_lobby: dict[int, str],
) -> Router:
    """создаёт роутер игры бункер: групповой режим и режим «отдельно»"""
    router = Router()
    locks: dict[int, asyncio.Lock] = {}

    async def _persist() -> None:
        await _persist_bunker(storage, sessions, lobbies)

    router.callback_query.middleware(make_chat_lock_middleware(locks))
    router.message.middleware(make_chat_lock_middleware(locks))
    router.callback_query.middleware(make_persist_middleware(_persist, _SCOPE))
    router.message.middleware(make_persist_middleware(_persist, _SCOPE))

    async def _open_group_lobby(target: Message, host_id: int) -> None:
        """создаёт групповое лобби бункера в чате target"""
        existing = sessions.get(target.chat.id)
        if existing is not None:
            if existing.phase != "lobby":
                await target.answer(
                    "Партия уже идёт. Создатель может нажать «Отменить игру»."
                )
                return
            # лобби уже открыто - не сбрасываем набранных игроков
            sent = await target.answer(
                _render_board(existing),
                reply_markup=_board_keyboard(existing),
            )
            existing.board_message_id = sent.message_id
            return
        session = BunkerSession(host_id=host_id, board_chat_id=target.chat.id)
        sessions[target.chat.id] = session
        sent = await target.answer(
            _render_board(session), reply_markup=_board_keyboard(session)
        )
        session.board_message_id = sent.message_id

    @router.message(Command("bunker"))
    async def handle_bunker(message: Message) -> None:
        """в группе открывает партию, в личке - лобби режима «отдельно»"""
        if message.from_user is None:
            return
        if message.chat.type == "private":
            host_id = message.from_user.id
            code = _generate_code(set(lobbies))
            lobby = SoloLobby(host_id=host_id, code=code)
            lobby.members[host_id] = message.from_user.full_name
            lobbies[code] = lobby
            member_lobby[host_id] = code
            sent = await message.answer(
                _render_solo_lobby(lobby),
                reply_markup=create_bunker_solo_lobby_keyboard(),
            )
            lobby.message_id = sent.message_id
            return
        if message.chat.type not in ("group", "supergroup"):
            return
        await _open_group_lobby(message, message.from_user.id)

    @router.callback_query(F.data == CB_BK_OPEN)
    async def handle_open(callback: CallbackQuery) -> None:
        """открывает лобби бункера из группового меню"""
        message = callback.message
        if not isinstance(message, Message):
            await callback.answer()
            return
        if message.chat.type not in ("group", "supergroup"):
            await callback.answer(
                "«Бункер» доступен в беседе.", show_alert=True
            )
            return
        await _open_group_lobby(message, callback.from_user.id)
        await callback.answer()

    @router.callback_query(F.data == CB_BK_JOIN)
    async def handle_join(callback: CallbackQuery) -> None:
        """добавляет игрока в лобби"""
        session = sessions.get(_chat_id(callback))
        if session is None or session.phase != "lobby":
            await callback.answer()
            return
        if len(session.players) >= MAX_PLAYERS:
            await callback.answer("Бункер переполнен.", show_alert=True)
            return
        session.players[callback.from_user.id] = callback.from_user.full_name
        await _edit_board(callback, session)
        await callback.answer("Ты в убежище.")

    @router.callback_query(F.data == CB_BK_MODE)
    async def handle_mode(callback: CallbackQuery) -> None:
        """переключает режим партии в лобби (создатель)"""
        session = sessions.get(_chat_id(callback))
        if session is None or session.phase != "lobby":
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Режим меняет создатель.", show_alert=True)
            return
        session.story_mode = not session.story_mode
        await _edit_board(callback, session)
        await callback.answer()

    @router.callback_query(F.data == CB_BK_START)
    async def handle_start(callback: CallbackQuery) -> None:
        """запускает партию: раздаёт карты и открывает первый раунд"""
        session = sessions.get(_chat_id(callback))
        if session is None or session.phase != "lobby":
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Начать может создатель.", show_alert=True)
            return
        count = len(session.players)
        if count < MIN_PLAYERS or count > MAX_PLAYERS:
            await callback.answer(
                f"Нужно от {MIN_PLAYERS} до {MAX_PLAYERS} игроков, сейчас {count}.",
                show_alert=True,
            )
            return

        bot = callback.bot
        if bot is None:
            await callback.answer()
            return
        # переиздаём руки только если состав изменился: иначе повтор «Начать»
        # после недоставки выдал бы уже получившим игрокам другие карты
        if set(session.hands) != set(session.players):
            session.hands = dict(
                zip(session.players, deal_hands(content, count), strict=True)
            )
        unreachable: list[str] = []
        for player_id, hand in session.hands.items():
            try:
                await bot.send_message(player_id, _render_hand(hand))
            except TelegramForbiddenError:
                unreachable.append(session.players[player_id])
        if unreachable:
            await callback.answer(
                "Не дошли карты: " + ", ".join(unreachable) + ". Им нужно "
                "открыть ЛС с ботом (/start) и снова нажать «Начать».",
                show_alert=True,
            )
            return

        session.catastrophe = pick_catastrophe(content)
        session.pairs = pick_pairs(content, ROUNDS_TOTAL)
        session.plan = rounds_plan(count)
        session.revealed_count = {player_id: 0 for player_id in session.players}
        _begin_round(session, 1)
        await bot.send_message(session.board_chat_id, _render_intro(session))
        await _post_board(callback, session)
        await callback.answer()

    @router.callback_query(F.data == CB_BK_REVEAL)
    async def handle_reveal(callback: CallbackQuery) -> None:
        """игрок открывает свою карту текущего раунда"""
        session = sessions.get(_chat_id(callback))
        if session is None or session.phase != "reveal":
            await callback.answer()
            return
        player_id = callback.from_user.id
        if player_id not in session.players:
            await callback.answer("Ты не в игре.", show_alert=True)
            return
        if player_id in session.excluded:
            await callback.answer("Изгнанные карт не открывают.", show_alert=True)
            return
        revealed = session.revealed_count.get(player_id, 0)
        if revealed >= session.round_no:
            await callback.answer("В этом раунде ты уже открыл карту.")
            return

        key = REVEAL_ORDER[revealed]
        card = getattr(session.hands[player_id], key)
        session.revealed_count[player_id] = revealed + 1
        bot = callback.bot
        if bot is not None:
            await bot.send_message(
                session.board_chat_id,
                f"🃏 {session.players[player_id]} открывает - "
                f"{BUNKER_CARD_LABELS[key]}: {card}",
            )
        await _edit_board(callback, session)
        await callback.answer("Карта открыта.")

    @router.callback_query(F.data == CB_BK_VOTE_START)
    async def handle_vote_start(callback: CallbackQuery) -> None:
        """создатель начинает голосование за изгнание"""
        session = sessions.get(_chat_id(callback))
        if session is None or session.phase != "reveal":
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Голосование запускает создатель.", show_alert=True)
            return
        if session.votes_pending <= 0:
            await callback.answer("В этом раунде голосования нет.", show_alert=True)
            return
        _open_vote(session, _alive(session))
        await _post_board(callback, session)
        await callback.answer()

    @router.callback_query(data_startswith(CB_BK_VOTE_PREFIX))
    async def handle_vote(callback: CallbackQuery) -> None:
        """принимает голос игрока против кандидата"""
        session = sessions.get(_chat_id(callback))
        if session is None or session.phase != "vote":
            await callback.answer()
            return
        voter_id = callback.from_user.id
        if voter_id not in session.players or voter_id in session.excluded:
            await callback.answer(
                "Голосуют только активные игроки.", show_alert=True
            )
            return
        candidate = _parse_int((callback.data or "")[len(CB_BK_VOTE_PREFIX):])
        if candidate is None or candidate not in session.vote_candidates:
            await callback.answer()
            return

        session.votes[voter_id] = candidate
        if len(session.votes) >= len(_alive(session)):
            await _close_vote(callback, session)
        else:
            await _edit_board(callback, session)
            await callback.answer("Голос учтён.")

    @router.callback_query(F.data == CB_BK_VOTE_TALLY)
    async def handle_vote_tally(callback: CallbackQuery) -> None:
        """создатель досрочно подводит итоги голосования"""
        session = sessions.get(_chat_id(callback))
        if session is None or session.phase != "vote":
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Итоги подводит создатель.", show_alert=True)
            return
        if len(session.votes) == 0:
            await callback.answer("Ещё никто не проголосовал.", show_alert=True)
            return
        await _close_vote(callback, session)

    @router.callback_query(F.data == CB_BK_NEXT)
    async def handle_next(callback: CallbackQuery) -> None:
        """создатель переходит к следующему раунду без голосования"""
        session = sessions.get(_chat_id(callback))
        if session is None or session.phase != "reveal":
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Раунд листает создатель.", show_alert=True)
            return
        if session.votes_pending > 0:
            await callback.answer("Сначала проведите голосование.", show_alert=True)
            return
        _begin_round(session, session.round_no + 1)
        await _announce_round(callback, session)
        await _post_board(callback, session)
        await callback.answer()

    @router.callback_query(F.data == CB_BK_CANCEL)
    async def handle_cancel(callback: CallbackQuery) -> None:
        """создатель отменяет партию"""
        session = sessions.get(_chat_id(callback))
        if session is None:
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Отменить может создатель.", show_alert=True)
            return
        sessions.pop(session.board_chat_id, None)
        await _replace_board(callback, "Партия в бункер отменена.")
        await callback.answer()

    @router.message(Command("joinbunker"))
    async def handle_join_solo(message: Message, command: CommandObject) -> None:
        """присоединяет игрока к режиму «отдельно» по коду"""
        if message.chat.type != "private":
            await message.answer("Команда /joinbunker работает в личке с ботом.")
            return
        if message.from_user is None:
            return
        code = (command.args or "").strip()
        lobby = lobbies.get(code)
        if lobby is None:
            await message.answer("Нет игры с таким кодом. Уточни код у создателя.")
            return
        if lobby.started:
            await message.answer("Эта партия уже началась.")
            return
        member_id = message.from_user.id
        if member_id in lobby.members:
            await message.answer("Ты уже в этой игре.")
            return
        if len(lobby.members) >= MAX_PLAYERS:
            await message.answer("Бункер переполнен.")
            return

        lobby.members[member_id] = message.from_user.full_name
        member_lobby[member_id] = code
        await message.answer(
            f"Ты в убежище. Код {code}. Жди старта от создателя."
        )
        bot = message.bot
        if bot is not None and lobby.message_id is not None:
            try:
                await bot.edit_message_text(
                    _render_solo_lobby(lobby),
                    chat_id=lobby.host_id,
                    message_id=lobby.message_id,
                    reply_markup=create_bunker_solo_lobby_keyboard(),
                )
            except TelegramBadRequest:
                pass

    @router.callback_query(F.data == CB_BK_SOLO_START)
    async def handle_solo_start(callback: CallbackQuery) -> None:
        """раздаёт карты участникам режима «отдельно»"""
        lobby = _lookup_lobby(callback.from_user.id, lobbies, member_lobby)
        if lobby is None or lobby.started:
            await callback.answer()
            return
        if callback.from_user.id != lobby.host_id:
            await callback.answer("Начать может создатель.", show_alert=True)
            return
        count = len(lobby.members)
        if count < MIN_PLAYERS or count > MAX_PLAYERS:
            await callback.answer(
                f"Нужно от {MIN_PLAYERS} до {MAX_PLAYERS} игроков, сейчас {count}.",
                show_alert=True,
            )
            return
        bot = callback.bot
        if bot is None:
            await callback.answer()
            return

        hands = deal_hands(content, count)
        intro = _render_solo_intro(
            pick_catastrophe(content),
            pick_pairs(content, ROUNDS_TOTAL),
            rounds_plan(count),
            count,
        )
        unreachable: list[str] = []
        for (member_id, name), hand in zip(
            lobby.members.items(), hands, strict=True
        ):
            try:
                await bot.send_message(member_id, _render_hand(hand))
                await bot.send_message(member_id, intro)
            except TelegramForbiddenError:
                unreachable.append(name)
        if unreachable:
            await callback.answer(
                "Не дошли карты: " + ", ".join(unreachable) + ". Им нужно "
                "написать боту /start и снова нажать «Начать».",
                show_alert=True,
            )
            return

        lobby.started = True
        _drop_lobby(lobby, lobbies, member_lobby)
        await _replace_board(
            callback,
            f"Карты розданы {count} игрокам. Играйте: открывайте карты по "
            "одной каждый раунд и голосуйте за изгнание сами.",
        )
        await callback.answer()

    @router.callback_query(F.data == CB_BK_SOLO_CANCEL)
    async def handle_solo_cancel(callback: CallbackQuery) -> None:
        """создатель закрывает лобби режима «отдельно»"""
        lobby = _lookup_lobby(callback.from_user.id, lobbies, member_lobby)
        if lobby is None:
            await callback.answer()
            return
        if callback.from_user.id != lobby.host_id:
            await callback.answer("Закрыть может создатель.", show_alert=True)
            return
        _drop_lobby(lobby, lobbies, member_lobby)
        await _replace_board(callback, "Лобби бункера закрыто.")
        await callback.answer()

    @router.callback_query(F.data.in_({CB_BK_STORY_YES, CB_BK_STORY_NO}))
    async def handle_story_vote(callback: CallbackQuery) -> None:
        """принимает голос «справились / не справились» в финале"""
        session = sessions.get(_chat_id(callback))
        if session is None or session.phase != "story":
            await callback.answer()
            return
        if callback.from_user.id not in session.players:
            await callback.answer("Голосуют только участники.", show_alert=True)
            return
        session.story_votes[callback.from_user.id] = callback.data == CB_BK_STORY_YES
        if len(session.story_votes) >= len(session.players):
            await _resolve_challenge(callback, session)
        else:
            await _edit_board(callback, session)
            await callback.answer("Голос учтён.")

    @router.callback_query(F.data == CB_BK_STORY_TALLY)
    async def handle_story_tally(callback: CallbackQuery) -> None:
        """создатель досрочно подводит итог испытания финала"""
        session = sessions.get(_chat_id(callback))
        if session is None or session.phase != "story":
            await callback.answer()
            return
        if callback.from_user.id != session.host_id:
            await callback.answer("Итог подводит создатель.", show_alert=True)
            return
        if len(session.story_votes) == 0:
            await callback.answer("Ещё никто не проголосовал.", show_alert=True)
            return
        await _resolve_challenge(callback, session)

    async def _close_vote(callback: CallbackQuery, session: BunkerSession) -> None:
        """подводит итог голосования: изгоняет или назначает переголосование"""
        leaders = vote_leaders(session.votes)
        bot = callback.bot
        if len(leaders) > 1 and not session.revote:
            names = ", ".join(session.players[c] for c in leaders)
            if bot is not None:
                await bot.send_message(
                    session.board_chat_id,
                    f"⚖️ Ничья: {names}. Переголосование среди них.",
                )
            session.revote = True
            _open_vote(session, leaders)
            await _post_board(callback, session)
            await callback.answer()
            return

        excluded_id = leaders[0] if len(leaders) == 1 else random.choice(leaders)
        _exclude_player(session, excluded_id)
        if bot is not None:
            await bot.send_message(
                session.board_chat_id, _render_exclusion(session, excluded_id)
            )
        session.votes_pending -= 1

        if session.votes_pending > 0:
            _open_vote(session, _alive(session))
            await _post_board(callback, session)
            await callback.answer()
            return
        if session.round_no >= ROUNDS_TOTAL:
            await _finale(callback, session)
            return
        _begin_round(session, session.round_no + 1)
        await _announce_round(callback, session)
        await _post_board(callback, session)
        await callback.answer()

    async def _announce_round(
        callback: CallbackQuery, session: BunkerSession
    ) -> None:
        """публикует исследование бункера нового раунда"""
        bot = callback.bot
        if bot is not None:
            await bot.send_message(
                session.board_chat_id, _render_pair(session, session.round_no)
            )

    async def _finale(callback: CallbackQuery, session: BunkerSession) -> None:
        """завершает партию: базовый итог либо история выживания"""
        if session.story_mode:
            await _start_story(callback, session)
            return
        bot = callback.bot
        if bot is not None:
            await bot.send_message(session.board_chat_id, _render_finale(session))
        sessions.pop(session.board_chat_id, None)
        await _replace_board(callback, "Бункер закрыт. Игра окончена.")
        await callback.answer()

    async def _start_story(
        callback: CallbackQuery, session: BunkerSession
    ) -> None:
        """запускает развязку «история выживания»"""
        session.survivors_bunker = _alive(session)
        session.survivors_exiles = list(session.excluded)
        session.finale_queue = _build_finale_queue(session, content)
        session.finale_index = 0
        bot = callback.bot
        if bot is not None:
            await bot.send_message(
                session.board_chat_id, _render_story_start(session)
            )
        await _present_challenge(callback, session)

    async def _present_challenge(
        callback: CallbackQuery, session: BunkerSession
    ) -> None:
        """показывает следующее испытание или подводит итог истории"""
        while session.finale_index < len(session.finale_queue):
            if _challenge_survivors(
                session, session.finale_queue[session.finale_index]
            ):
                break
            session.finale_index += 1
        if session.finale_index >= len(session.finale_queue):
            await _story_verdict(callback, session)
            return
        session.phase = "story"
        session.story_votes = {}
        bot = callback.bot
        if bot is not None:
            await bot.send_message(
                session.board_chat_id, _render_challenge(session)
            )
        await _post_board(callback, session)
        await callback.answer()

    async def _resolve_challenge(
        callback: CallbackQuery, session: BunkerSession
    ) -> None:
        """разыгрывает итог испытания: успех либо случайная потеря"""
        challenge = session.finale_queue[session.finale_index]
        yes = sum(1 for survived in session.story_votes.values() if survived)
        survived = yes * 2 >= len(session.players)
        bot = callback.bot
        if bot is not None:
            await bot.send_message(
                session.board_chat_id,
                _render_outcome(challenge, survived),
            )
        if not survived:
            casualty = _apply_casualty(session, challenge)
            if bot is not None:
                await bot.send_message(session.board_chat_id, casualty)
        session.finale_index += 1
        await _present_challenge(callback, session)

    async def _story_verdict(
        callback: CallbackQuery, session: BunkerSession
    ) -> None:
        """объявляет, кто пережил историю выживания"""
        bot = callback.bot
        if bot is not None:
            await bot.send_message(
                session.board_chat_id, _render_story_verdict(session)
            )
        sessions.pop(session.board_chat_id, None)
        await _replace_board(callback, "История выживания завершена.")
        await callback.answer()

    return router


def _dump_plan(plan: RoundsPlan) -> dict[str, Any]:
    """сериализует план раундов"""
    return {
        "votes_per_round": list(plan.votes_per_round),
        "exclusions": plan.exclusions,
        "seats": plan.seats,
    }


def _load_plan(data: dict[str, Any]) -> RoundsPlan:
    """восстанавливает план раундов из словаря"""
    votes = data["votes_per_round"]
    return RoundsPlan(
        votes_per_round=(votes[0], votes[1], votes[2], votes[3], votes[4]),
        exclusions=data["exclusions"],
        seats=data["seats"],
    )


def _dump_bunker(session: BunkerSession) -> dict[str, Any]:
    """сериализует партию бункера в словарь"""
    return {
        "host_id": session.host_id,
        "board_chat_id": session.board_chat_id,
        "phase": session.phase,
        "board_message_id": session.board_message_id,
        "story_mode": session.story_mode,
        "catastrophe": session.catastrophe,
        "pairs": [list(pair) for pair in session.pairs],
        "plan": _dump_plan(session.plan) if session.plan is not None else None,
        "round_no": session.round_no,
        "votes_pending": session.votes_pending,
        "revote": session.revote,
        "players": session.players,
        "hands": {
            str(pid): asdict(hand) for pid, hand in session.hands.items()
        },
        "revealed_count": session.revealed_count,
        "excluded": list(session.excluded),
        "votes": session.votes,
        "vote_candidates": session.vote_candidates,
        "survivors_bunker": session.survivors_bunker,
        "survivors_exiles": session.survivors_exiles,
        "finale_queue": [asdict(ch) for ch in session.finale_queue],
        "finale_index": session.finale_index,
        "story_votes": session.story_votes,
    }


def _load_bunker(data: dict[str, Any]) -> BunkerSession:
    """восстанавливает партию бункера из словаря"""
    session = BunkerSession(
        host_id=data["host_id"], board_chat_id=data["board_chat_id"]
    )
    session.phase = data["phase"]
    session.board_message_id = data["board_message_id"]
    session.story_mode = data["story_mode"]
    session.catastrophe = data["catastrophe"]
    session.pairs = [(pair[0], pair[1]) for pair in data["pairs"]]
    plan = data["plan"]
    session.plan = _load_plan(plan) if plan is not None else None
    session.round_no = data["round_no"]
    session.votes_pending = data["votes_pending"]
    session.revote = data["revote"]
    session.players = {int(k): v for k, v in data["players"].items()}
    session.hands = {
        int(k): PlayerHand(**hand) for k, hand in data["hands"].items()
    }
    session.revealed_count = {
        int(k): v for k, v in data["revealed_count"].items()
    }
    session.excluded = {int(pid) for pid in data["excluded"]}
    session.votes = {int(k): v for k, v in data["votes"].items()}
    session.vote_candidates = list(data["vote_candidates"])
    session.survivors_bunker = list(data["survivors_bunker"])
    session.survivors_exiles = list(data["survivors_exiles"])
    session.finale_queue = [Challenge(**ch) for ch in data["finale_queue"]]
    session.finale_index = data["finale_index"]
    session.story_votes = {int(k): v for k, v in data["story_votes"].items()}
    return session


def _dump_lobby(lobby: SoloLobby) -> dict[str, Any]:
    """сериализует лобби режима «отдельно»"""
    return {
        "host_id": lobby.host_id,
        "code": lobby.code,
        "message_id": lobby.message_id,
        "started": lobby.started,
        "members": lobby.members,
    }


def _load_lobby(data: dict[str, Any]) -> SoloLobby:
    """восстанавливает лобби режима «отдельно» из словаря"""
    lobby = SoloLobby(host_id=data["host_id"], code=data["code"])
    lobby.message_id = data["message_id"]
    lobby.started = data["started"]
    lobby.members = {int(k): v for k, v in data["members"].items()}
    return lobby


async def _persist_bunker(
    storage: SQLiteHistoryStorage,
    sessions: dict[int, BunkerSession],
    lobbies: dict[str, SoloLobby],
) -> None:
    """сохраняет снапшот партий бункера и лобби режима «отдельно»"""
    await storage.replace_session_scope(
        _SCOPE,
        {
            str(chat_id): json.dumps(_dump_bunker(session))
            for chat_id, session in sessions.items()
        },
    )
    await storage.replace_session_scope(
        _LOBBY_SCOPE,
        {
            lobby.code: json.dumps(_dump_lobby(lobby))
            for lobby in lobbies.values()
        },
    )


async def restore_bunker_sessions(
    storage: SQLiteHistoryStorage,
    sessions: dict[int, BunkerSession],
    lobbies: dict[str, SoloLobby],
    member_lobby: dict[int, str],
) -> None:
    """наполняет партии, лобби и индекс участников из хранилища при старте"""
    raw_sessions = await storage.load_session_scope(_SCOPE)
    for key, data in raw_sessions.items():
        sessions[int(key)] = _load_bunker(json.loads(data))
    raw_lobbies = await storage.load_session_scope(_LOBBY_SCOPE)
    for data in raw_lobbies.values():
        lobby = _load_lobby(json.loads(data))
        lobbies[lobby.code] = lobby
        for member_id in lobby.members:
            member_lobby[member_id] = lobby.code


def _begin_round(session: BunkerSession, round_no: int) -> None:
    """открывает новый раунд: пара бункер+угроза и план голосований"""
    plan = session.plan
    session.round_no = round_no
    session.phase = "reveal"
    session.votes_pending = plan.votes_per_round[round_no - 1] if plan else 0
    session.votes = {}
    session.vote_candidates = []
    session.revote = False


def _open_vote(session: BunkerSession, candidates: list[int]) -> None:
    """начинает голосование среди заданных кандидатов"""
    session.phase = "vote"
    session.votes = {}
    session.vote_candidates = candidates


def _exclude_player(session: BunkerSession, player_id: int) -> None:
    """изгоняет игрока и раскрывает все его карты персонажа"""
    session.excluded.add(player_id)
    session.revealed_count[player_id] = len(REVEAL_ORDER)
    session.phase = "reveal"
    session.revote = False


def _alive(session: BunkerSession) -> list[int]:
    """возвращает id игроков, не изгнанных из игры"""
    return [pid for pid in session.players if pid not in session.excluded]


async def _post_board(callback: CallbackQuery, session: BunkerSession) -> None:
    """публикует новое сообщение-табло (на смене фазы)"""
    bot = callback.bot
    if bot is None:
        return
    sent = await bot.send_message(
        session.board_chat_id,
        _render_board(session),
        reply_markup=_board_keyboard(session),
    )
    session.board_message_id = sent.message_id


async def _edit_board(callback: CallbackQuery, session: BunkerSession) -> None:
    """правит сообщение-табло на месте, публикует новое если его ещё нет"""
    bot = callback.bot
    if bot is None:
        return
    if session.board_message_id is None:
        await _post_board(callback, session)
        return
    try:
        await bot.edit_message_text(
            _render_board(session),
            chat_id=session.board_chat_id,
            message_id=session.board_message_id,
            reply_markup=_board_keyboard(session),
        )
    except TelegramBadRequest:
        pass


async def _replace_board(callback: CallbackQuery, text: str) -> None:
    """заменяет сообщение-табло финальным текстом без клавиатуры"""
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text)
        except TelegramBadRequest:
            pass


def _board_keyboard(session: BunkerSession) -> InlineKeyboardMarkup:
    """строит клавиатуру под текущую фазу партии"""
    if session.phase == "vote":
        candidates = [
            (candidate_id, session.players[candidate_id])
            for candidate_id in session.vote_candidates
        ]
        return create_bunker_vote_keyboard(candidates)
    if session.phase == "reveal":
        return create_bunker_reveal_keyboard(session.votes_pending > 0)
    if session.phase == "story":
        return create_bunker_story_keyboard()
    return create_bunker_lobby_keyboard(session.story_mode)


def _render_board(session: BunkerSession) -> str:
    """отображает табло партии под текущую фазу"""
    if session.phase == "lobby":
        return _render_lobby(session)
    if session.phase == "story":
        return _render_story_board(session)

    plan = session.plan
    seats = plan.seats if plan else 0
    left = (plan.exclusions - len(session.excluded)) if plan else 0
    lines = [
        f"🏚 Бункер - раунд {session.round_no}/{ROUNDS_TOTAL}",
        f"☢️ Катастрофа: {session.catastrophe}",
        f"🚪 Мест в бункере: {seats} | под изгнание осталось: {left}",
        f"📦 Открыто пар бункер+угроза: {session.round_no}/{ROUNDS_TOTAL}",
        "",
        "👥 Игроки:",
    ]
    for player_id, name in session.players.items():
        mark = "❌" if player_id in session.excluded else "✅"
        count = session.revealed_count.get(player_id, 0)
        suffix = " (изгнан)" if player_id in session.excluded else ""
        lines.append(f"{mark} {name} - открыто карт: {count}{suffix}")
    lines.append("")

    if session.phase == "reveal":
        alive = _alive(session)
        opened = [p for p in alive if session.revealed_count.get(p, 0) >= session.round_no]
        lines.append(f"Откройте по карте. Открыли: {len(opened)}/{len(alive)}")
        lines.append(
            "Дальше - голосование за изгнание."
            if session.votes_pending > 0
            else "Голосования в этом раунде нет."
        )
    elif session.phase == "vote":
        scope = " (переголосование)" if session.revote else ""
        lines.append(
            f"Голосование за изгнание{scope}. "
            f"Проголосовали: {len(session.votes)}/{len(_alive(session))}."
        )
        lines.append("Жмите кандидата - голос тайный.")
    return "\n".join(lines)


def _render_lobby(session: BunkerSession) -> str:
    """отображает лобби сбора игроков"""
    mode = "история выживания" if session.story_mode else "базовый"
    lines = [
        "🏚 Бункер. Сбор в убежище.",
        f"Режим: {mode}.",
        "",
        "Игроки:",
    ]
    if session.players:
        lines.extend(f"- {name}" for name in session.players.values())
    else:
        lines.append("- пока никого")
    lines.extend(
        [
            "",
            f"Нужно {MIN_PLAYERS}-{MAX_PLAYERS} игроков. Карты придут в личку - "
            "откройте ЛС с ботом заранее (/start). Создатель жмёт «Начать».",
        ]
    )
    return "\n".join(lines)


def _render_intro(session: BunkerSession) -> str:
    """отображает вступление: катастрофа и план партии"""
    plan = session.plan
    seats = plan.seats if plan else 0
    exclusions = plan.exclusions if plan else 0
    mode = "история выживания" if session.story_mode else "базовый"
    return (
        "☢️ КАТАСТРОФА\n"
        f"{session.catastrophe}\n\n"
        f"Режим: {mode}. Игроков: {len(session.players)}. "
        f"Мест в бункере: {seats}. Будет изгнано: {exclusions}.\n"
        "Карты персонажа разосланы в личку. Особое условие можно разыграть "
        "голосом в любой момент.\n\n"
        f"{_render_pair(session, 1)}"
    )


def _render_pair(session: BunkerSession, round_no: int) -> str:
    """отображает пару карт бункера и угрозы данного раунда"""
    item, threat = session.pairs[round_no - 1]
    return (
        f"📦 Исследование бункера (раунд {round_no})\n"
        f"Бункер: {item}\n"
        f"⚠️ Угроза: {threat}"
    )


def _render_hand(hand: PlayerHand) -> str:
    """отображает личный набор карт персонажа"""
    return (
        "🎒 Твой персонаж (втайне):\n"
        f"{BUNKER_CARD_LABELS['superpower']}: {hand.superpower}\n"
        f"{BUNKER_CARD_LABELS['phobia']}: {hand.phobia}\n"
        f"{BUNKER_CARD_LABELS['character']}: {hand.character}\n"
        f"{BUNKER_CARD_LABELS['hobby']}: {hand.hobby}\n"
        f"{BUNKER_CARD_LABELS['baggage']}: {hand.baggage}\n"
        f"{BUNKER_CARD_LABELS['fact']}: {hand.fact}\n"
        f"{BUNKER_CARD_LABELS['special_condition']}: {hand.special_condition}\n\n"
        "Карты раскрываются по одной каждый раунд, факт - в финале."
    )


def _render_exclusion(session: BunkerSession, player_id: int) -> str:
    """отображает изгнание игрока с раскрытием всех карт"""
    hand = session.hands[player_id]
    return (
        f"🚫 Изгнан: {session.players[player_id]}\n"
        f"{BUNKER_CARD_LABELS['superpower']}: {hand.superpower}\n"
        f"{BUNKER_CARD_LABELS['phobia']}: {hand.phobia}\n"
        f"{BUNKER_CARD_LABELS['character']}: {hand.character}\n"
        f"{BUNKER_CARD_LABELS['hobby']}: {hand.hobby}\n"
        f"{BUNKER_CARD_LABELS['baggage']}: {hand.baggage}\n"
        f"{BUNKER_CARD_LABELS['fact']}: {hand.fact}"
    )


def _render_finale(session: BunkerSession) -> str:
    """отображает финал базового режима: состав бункера"""
    survivors = _alive(session)
    lines = ["🚪 ФИНАЛ", ""]
    if survivors:
        lines.append("В бункер попали (победители):")
        for player_id in survivors:
            hand = session.hands[player_id]
            lines.append(
                f"🏆 {session.players[player_id]} - "
                f"{hand.superpower}; {hand.character}; {hand.fact}"
            )
    else:
        lines.append("В бункер не попал никто.")
    excluded_names = [session.players[pid] for pid in session.excluded]
    if excluded_names:
        lines.extend(["", "Снаружи остались: " + ", ".join(excluded_names)])
    return "\n".join(lines)


def _build_finale_queue(
    session: BunkerSession, content: BunkerContent
) -> list[Challenge]:
    """собирает очередь испытаний финала «история выживания»"""
    queue: list[Challenge] = []
    if session.survivors_bunker:
        threat = random.choice([threat for _, threat in session.pairs])
        queue.append(Challenge(group="bunker", kind="threat", text=threat))
    if session.survivors_exiles:
        for threat in random.sample(content.threats, 2):
            queue.append(Challenge(group="exiles", kind="threat", text=threat))
    queue.append(
        Challenge(group="all", kind="catastrophe", text=session.catastrophe)
    )
    return queue


def _challenge_survivors(
    session: BunkerSession, challenge: Challenge
) -> list[int]:
    """возвращает живых членов группы данного испытания"""
    if challenge.group == "bunker":
        return session.survivors_bunker
    if challenge.group == "exiles":
        return session.survivors_exiles
    return _story_survivors(session)


def _story_survivors(session: BunkerSession) -> list[int]:
    """возвращает всех выживших обеих групп"""
    return session.survivors_bunker + session.survivors_exiles


def _apply_casualty(session: BunkerSession, challenge: Challenge) -> str:
    """разыгрывает потерю при провале испытания и описывает её"""
    if challenge.kind == "catastrophe":
        victims = _story_survivors(session)
        session.survivors_bunker = []
        session.survivors_exiles = []
        names = ", ".join(session.players[pid] for pid in victims)
        return f"☢️ Катастрофа сильнее. Погибли все: {names}."

    is_bunker = challenge.group == "bunker"
    group = (
        session.survivors_bunker if is_bunker else session.survivors_exiles
    )
    # 0 - маркер карты угрозы; id игрока в telegram всегда положительный
    pick = random.choice([*group, 0])
    if pick == 0:
        names = ", ".join(session.players[pid] for pid in group)
        if is_bunker:
            session.survivors_bunker = []
        else:
            session.survivors_exiles = []
        return f"💀 Фатальная неудача: погибла вся группа ({names})."
    group.remove(pick)
    return f"⚰️ Погибает: {session.players[pick]}."


def _challenge_header(challenge: Challenge) -> str:
    """возвращает заголовок испытания по его группе"""
    return {
        "bunker": "Угроза в бункере",
        "exiles": "Угроза изгнанным",
        "all": "Финальная катастрофа",
    }[challenge.group]


def _render_story_start(session: BunkerSession) -> str:
    """отображает старт истории выживания: состав групп"""
    bunker = ", ".join(
        session.players[pid] for pid in session.survivors_bunker
    )
    exiles = ", ".join(
        session.players[pid] for pid in session.survivors_exiles
    )
    return "\n".join(
        [
            "🎬 История выживания",
            f"В бункере: {bunker or 'никого'}.",
            f"Снаружи (изгнанные): {exiles or 'никого'}.",
            "Проверим, кто переживёт угрозы и катастрофу. Голосуют все.",
        ]
    )


def _render_challenge(session: BunkerSession) -> str:
    """отображает объявление нового испытания"""
    challenge = session.finale_queue[session.finale_index]
    icon = "☢️" if challenge.kind == "catastrophe" else "⚠️"
    names = ", ".join(
        session.players[pid]
        for pid in _challenge_survivors(session, challenge)
    )
    return "\n".join(
        [
            f"{icon} {_challenge_header(challenge)}",
            challenge.text,
            "",
            f"Под угрозой: {names}.",
            "Голосуйте: хватит ли у группы трёх полезных карт?",
        ]
    )


def _render_story_board(session: BunkerSession) -> str:
    """отображает табло текущего испытания истории выживания"""
    challenge = session.finale_queue[session.finale_index]
    return "\n".join(
        [
            "🎬 История выживания",
            f"{_challenge_header(challenge)}: {challenge.text}",
            f"Проголосовали: {len(session.story_votes)}/{len(session.players)}.",
            "Жмите «Справились» или «Не справились».",
        ]
    )


def _render_outcome(challenge: Challenge, survived: bool) -> str:
    """отображает исход голосования по испытанию"""
    header = _challenge_header(challenge)
    if survived:
        return f"✅ {header}: группа справилась с «{challenge.text}»."
    return f"❌ {header}: не справились с «{challenge.text}»."


def _render_story_verdict(session: BunkerSession) -> str:
    """отображает итог истории выживания"""
    survivors = _story_survivors(session)
    if survivors:
        names = ", ".join(session.players[pid] for pid in survivors)
        return f"🏆 ИТОГ ИСТОРИИ ВЫЖИВАНИЯ\nВыжили: {names}. Поздравляем!"
    return "☠️ ИТОГ ИСТОРИИ ВЫЖИВАНИЯ\nНе выжил никто."


def _render_solo_lobby(lobby: SoloLobby) -> str:
    """отображает лобби режима «отдельно» с кодом и составом"""
    lines = [
        f"🏚 Бункер - режим «отдельно». Код: {lobby.code}",
        "",
        f"Игроки открывают ЛС с ботом и вводят: /joinbunker {lobby.code}",
        "",
        "Состав:",
    ]
    lines.extend(f"- {name}" for name in lobby.members.values())
    lines.extend(
        [
            "",
            f"Нужно {MIN_PLAYERS}-{MAX_PLAYERS} игроков. Карты придут каждому в "
            "личку. Создатель жмёт «Начать».",
        ]
    )
    return "\n".join(lines)


def _render_solo_intro(
    catastrophe: str,
    pairs: list[tuple[str, str]],
    plan: RoundsPlan,
    count: int,
) -> str:
    """отображает общий стол режима «отдельно»: катастрофа, план, пары"""
    lines = [
        "☢️ КАТАСТРОФА",
        catastrophe,
        "",
        f"Игроков: {count}. Мест в бункере: {plan.seats}. "
        f"Изгнать: {plan.exclusions}.",
        "",
        "📦 Пары бункер+угроза по раундам:",
    ]
    for index, (item, threat) in enumerate(pairs, start=1):
        lines.append(f"{index}. {item} / ⚠️ {threat}")
    lines.extend(
        [
            "",
            "Раскрывайте по одной карте каждый раунд (суперсила, фобия, "
            "характер, хобби, багаж; факт - в финале) и голосуйте за изгнание "
            "сами. После 5 раундов оставшиеся попадают в бункер.",
        ]
    )
    return "\n".join(lines)


def _generate_code(existing: set[str]) -> str:
    """генерирует уникальный четырёхзначный код лобби"""
    for _ in range(100):
        code = str(random.randint(1000, 9999))
        if code not in existing:
            return code
    raise RuntimeError("не удалось сгенерировать код лобби бункера")


def _lookup_lobby(
    user_id: int, lobbies: dict[str, SoloLobby], member_lobby: dict[int, str]
) -> SoloLobby | None:
    """находит лобби режима «отдельно» по участнику"""
    code = member_lobby.get(user_id)
    return lobbies.get(code) if code is not None else None


def _drop_lobby(
    lobby: SoloLobby,
    lobbies: dict[str, SoloLobby],
    member_lobby: dict[int, str],
) -> None:
    """удаляет лобби и записи его участников из реестров"""
    for member_id in lobby.members:
        member_lobby.pop(member_id, None)
    lobbies.pop(lobby.code, None)


def _chat_id(callback: CallbackQuery) -> int:
    """возвращает id чата сообщения callback"""
    message = callback.message
    return message.chat.id if isinstance(message, Message) else 0


def _parse_int(value: str) -> int | None:
    """разбирает целое число из строки callback-данных"""
    try:
        return int(value)
    except ValueError:
        return None
