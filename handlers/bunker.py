import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from constants import (
    BUNKER_CARD_LABELS,
    CB_BK_CANCEL,
    CB_BK_JOIN,
    CB_BK_NEXT,
    CB_BK_REVEAL,
    CB_BK_SOLO_CANCEL,
    CB_BK_SOLO_START,
    CB_BK_START,
    CB_BK_VOTE_PREFIX,
    CB_BK_VOTE_START,
    CB_BK_VOTE_TALLY,
)
from keyboards import (
    create_bunker_lobby_keyboard,
    create_bunker_reveal_keyboard,
    create_bunker_solo_lobby_keyboard,
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


@dataclass
class BunkerSession:
    """состояние партии в бункер в одном чате"""

    host_id: int
    board_chat_id: int
    phase: str = "lobby"
    board_message_id: int | None = None
    catastrophe: str = ""
    pairs: list[tuple[str, str]] = field(default_factory=list)
    plan: RoundsPlan | None = None
    round_no: int = 0
    pairs_open: int = 0
    votes_pending: int = 0
    revote: bool = False
    players: dict[int, str] = field(default_factory=dict)
    hands: dict[int, PlayerHand] = field(default_factory=dict)
    revealed_count: dict[int, int] = field(default_factory=dict)
    excluded: set[int] = field(default_factory=set)
    votes: dict[int, int] = field(default_factory=dict)
    vote_candidates: list[int] = field(default_factory=list)


@dataclass
class SoloLobby:
    """лёгкое лобби режима «отдельно»: раздаёт карты в личку по коду"""

    host_id: int
    code: str
    message_id: int | None = None
    started: bool = False
    members: dict[int, str] = field(default_factory=dict)


def create_bunker_router(content: BunkerContent) -> Router:
    """создаёт роутер игры бункер: групповой режим и режим «отдельно»"""
    router = Router()
    sessions: dict[int, BunkerSession] = {}
    lobbies: dict[str, SoloLobby] = {}
    member_lobby: dict[int, str] = {}

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
        existing = sessions.get(message.chat.id)
        if existing is not None and existing.phase != "lobby":
            await message.answer(
                "Партия уже идёт. Создатель может нажать «Отменить игру»."
            )
            return

        session = BunkerSession(
            host_id=message.from_user.id if message.from_user else 0,
            board_chat_id=message.chat.id,
        )
        sessions[message.chat.id] = session
        sent = await message.answer(
            _render_board(session), reply_markup=_board_keyboard(session)
        )
        session.board_message_id = sent.message_id

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
        await _show_board(callback, session, False)
        await callback.answer("Ты в убежище.")

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
        hands = deal_hands(content, count)
        assignments = dict(zip(session.players, hands, strict=True))
        unreachable: list[str] = []
        for player_id, hand in assignments.items():
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

        session.hands = assignments
        session.catastrophe = pick_catastrophe(content)
        session.pairs = pick_pairs(content, ROUNDS_TOTAL)
        session.plan = rounds_plan(count)
        session.revealed_count = {player_id: 0 for player_id in session.players}
        _begin_round(session, 1)
        await bot.send_message(session.board_chat_id, _render_intro(session))
        await _show_board(callback, session, True)
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
        await _show_board(callback, session, False)
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
        await _show_board(callback, session, True)
        await callback.answer()

    @router.callback_query(_startswith(CB_BK_VOTE_PREFIX))
    async def handle_vote(callback: CallbackQuery) -> None:
        """принимает голос игрока против кандидата"""
        session = sessions.get(_chat_id(callback))
        if session is None or session.phase != "vote":
            await callback.answer()
            return
        voter_id = callback.from_user.id
        if voter_id not in session.players:
            await callback.answer("Голосуют только участники.", show_alert=True)
            return
        candidate = _parse_int((callback.data or "")[len(CB_BK_VOTE_PREFIX):])
        if candidate is None or candidate not in session.vote_candidates:
            await callback.answer()
            return

        session.votes[voter_id] = candidate
        if len(session.votes) >= len(session.players):
            await _close_vote(callback, session)
        else:
            await _show_board(callback, session, False)
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
        await _show_board(callback, session, True)
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

    async def _close_vote(callback: CallbackQuery, session: BunkerSession) -> None:
        """подводит итог голосования: изгоняет или назначает переголосование"""
        leaders, _ = vote_leaders(session.votes)
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
            await _show_board(callback, session, True)
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
            await _show_board(callback, session, True)
            await callback.answer()
            return
        if session.round_no >= ROUNDS_TOTAL:
            await _finale(callback, session)
            return
        _begin_round(session, session.round_no + 1)
        await _announce_round(callback, session)
        await _show_board(callback, session, True)
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
        """завершает базовый режим: объявляет состав бункера"""
        bot = callback.bot
        if bot is not None:
            await bot.send_message(session.board_chat_id, _render_finale(session))
        sessions.pop(session.board_chat_id, None)
        await _replace_board(callback, "Бункер закрыт. Игра окончена.")
        await callback.answer()

    return router


def _begin_round(session: BunkerSession, round_no: int) -> None:
    """открывает новый раунд: пара бункер+угроза и план голосований"""
    plan = session.plan
    session.round_no = round_no
    session.pairs_open = round_no
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


async def _show_board(
    callback: CallbackQuery, session: BunkerSession, fresh: bool
) -> None:
    """обновляет табло: новое сообщение на смене фазы, иначе правка на месте"""
    bot = callback.bot
    if bot is None:
        return
    text = _render_board(session)
    keyboard = _board_keyboard(session)
    if fresh or session.board_message_id is None:
        sent = await bot.send_message(
            session.board_chat_id, text, reply_markup=keyboard
        )
        session.board_message_id = sent.message_id
        return
    try:
        await bot.edit_message_text(
            text,
            chat_id=session.board_chat_id,
            message_id=session.board_message_id,
            reply_markup=keyboard,
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
    return create_bunker_lobby_keyboard()


def _render_board(session: BunkerSession) -> str:
    """отображает табло партии под текущую фазу"""
    if session.phase == "lobby":
        return _render_lobby(session)

    plan = session.plan
    seats = plan.seats if plan else 0
    left = (plan.exclusions - len(session.excluded)) if plan else 0
    lines = [
        f"🏚 Бункер - раунд {session.round_no}/{ROUNDS_TOTAL}",
        f"☢️ Катастрофа: {session.catastrophe}",
        f"🚪 Мест в бункере: {seats} | под изгнание осталось: {left}",
        f"📦 Открыто пар бункер+угроза: {session.pairs_open}/{ROUNDS_TOTAL}",
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
            f"Проголосовали: {len(session.votes)}/{len(session.players)}."
        )
        lines.append("Жмите кандидата - голос тайный.")
    return "\n".join(lines)


def _render_lobby(session: BunkerSession) -> str:
    """отображает лобби сбора игроков"""
    lines = ["🏚 Бункер. Сбор в убежище.", "", "Игроки:"]
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
    return (
        "☢️ КАТАСТРОФА\n"
        f"{session.catastrophe}\n\n"
        f"Игроков: {len(session.players)}. Мест в бункере: {seats}. "
        f"Будет изгнано: {exclusions}.\n"
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


def _startswith(prefix: str) -> Callable[[CallbackQuery], bool]:
    """фильтр callback по префиксу данных"""
    return lambda callback: (
        callback.data is not None and callback.data.startswith(prefix)
    )


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
