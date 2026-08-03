import json
import logging
import random

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from constants import (
    BUNKER_CARD_LABELS,
    CB_BK_CANCEL,
    CB_BK_JOIN,
    CB_BK_LEAVE,
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
from database import DatabaseError, SQLiteHistoryStorage
from handlers.bunker_render import (
    board_keyboard,
    render_board,
    render_challenge,
    render_exclusion,
    render_finale,
    render_hand,
    render_intro,
    render_outcome,
    render_pair,
    render_solo_intro,
    render_solo_lobby,
    render_story_start,
    render_story_verdict,
)
from handlers.common import (
    ChatLocks,
    data_startswith,
    is_chat_manager,
    make_chat_lock_middleware,
    send_with_retry,
)
from keyboards import create_bunker_solo_lobby_keyboard
from services.bunker import (
    MAX_PLAYERS,
    MIN_PLAYERS,
    REVEAL_ORDER,
    BunkerContent,
    deal_hands,
    pick_catastrophe,
    pick_pairs,
    rounds_plan,
    vote_leaders,
)
from services.bunker_state import (
    ROUNDS_TOTAL,
    BunkerSession,
    SoloLobby,
    alive,
    apply_casualty,
    begin_round,
    build_finale_queue,
    challenge_survivors,
    drop_lobby,
    dump_lobby,
    dump_session,
    exclude_player,
    generate_code,
    leave_current_lobby,
    load_lobby,
    load_session,
    lookup_lobby,
    normalize_code,
    open_vote,
)

logger = logging.getLogger(__name__)

_SCOPE = "bunker"
_LOBBY_SCOPE = "bunker_lobby"


def create_bunker_router(
    content: BunkerContent,
    storage: SQLiteHistoryStorage,
    sessions: dict[int, BunkerSession],
    lobbies: dict[str, SoloLobby],
    member_lobby: dict[int, str],
    bot_username: str,
) -> Router:
    """создаёт роутер игры бункер: групповой режим и режим «отдельно»"""
    router = Router()
    locks = ChatLocks()

    router.callback_query.middleware(make_chat_lock_middleware(locks))
    router.message.middleware(make_chat_lock_middleware(locks))

    async def _save_session(chat_id: int) -> None:
        """точечно пишет снапшот партии одного чата"""
        try:
            session = sessions.get(chat_id)
            if session is None:
                await storage.delete_session(_SCOPE, str(chat_id))
                return
            await storage.save_session(
                _SCOPE, str(chat_id), json.dumps(dump_session(session))
            )
        except DatabaseError:
            logger.exception("session_persist_failed", extra={"scope": _SCOPE})

    async def _save_lobby(code: str | None) -> None:
        """точечно пишет снапшот одного лобби режима «отдельно»"""
        if code is None:
            return
        try:
            lobby = lobbies.get(code)
            if lobby is None:
                await storage.delete_session(_LOBBY_SCOPE, code)
                return
            await storage.save_session(
                _LOBBY_SCOPE, code, json.dumps(dump_lobby(lobby))
            )
        except DatabaseError:
            logger.exception("session_persist_failed", extra={"scope": _LOBBY_SCOPE})

    async def _open_group_lobby(target: Message, host_id: int) -> None:
        """создаёт групповое лобби бункера в чате target"""
        existing = sessions.get(target.chat.id)
        if existing is not None:
            if existing.phase != "lobby":
                await target.answer(
                    "Партия уже идёт. Создатель или админ чата может нажать "
                    "«Отменить игру»."
                )
                return
            # лобби уже открыто - не сбрасываем набранных игроков
            sent = await target.answer(
                render_board(existing),
                reply_markup=board_keyboard(existing, bot_username),
            )
            existing.board_message_id = sent.message_id
            await _save_session(target.chat.id)
            return
        session = BunkerSession(host_id=host_id, board_chat_id=target.chat.id)
        sessions[target.chat.id] = session
        sent = await target.answer(
            render_board(session), reply_markup=board_keyboard(session, bot_username)
        )
        session.board_message_id = sent.message_id
        await _save_session(target.chat.id)

    @router.message(Command("bunker"))
    async def handle_bunker(message: Message) -> None:
        """в группе открывает партию, в личке - лобби режима «отдельно»"""
        if message.from_user is None:
            return
        if message.chat.type == "private":
            host_id = message.from_user.id
            previous_code = leave_current_lobby(host_id, lobbies, member_lobby)
            code = generate_code(set(lobbies))
            lobby = SoloLobby(host_id=host_id, code=code)
            lobby.members[host_id] = message.from_user.full_name
            lobbies[code] = lobby
            member_lobby[host_id] = code
            sent = await message.answer(
                render_solo_lobby(lobby),
                reply_markup=create_bunker_solo_lobby_keyboard(),
            )
            lobby.message_id = sent.message_id
            await _save_lobby(previous_code)
            await _save_lobby(code)
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
            await callback.answer("«Бункер» доступен в беседе.", show_alert=True)
            return
        await _open_group_lobby(message, callback.from_user.id)
        await callback.answer()

    @router.callback_query(F.data == CB_BK_JOIN)
    async def handle_join(callback: CallbackQuery) -> None:
        """добавляет игрока в лобби и сразу проверяет, открыта ли его личка"""
        session = sessions.get(_chat_id(callback))
        if session is None or session.phase != "lobby":
            await callback.answer()
            return
        if len(session.players) >= MAX_PLAYERS:
            await callback.answer("Бункер переполнен.", show_alert=True)
            return
        player_id = callback.from_user.id
        session.players[player_id] = callback.from_user.full_name
        reachable = await _probe_private_chat(callback.bot, player_id)
        if reachable:
            session.reachable.add(player_id)
        else:
            session.reachable.discard(player_id)
        await _show_board(callback.bot, session, bot_username)
        await _save_session(session.board_chat_id)
        if reachable:
            await callback.answer("Ты в убежище, личка на связи.")
            return
        await callback.answer(
            "Ты в убежище, но личка закрыта: нажми /start в личке с ботом "
            "и вступи заново, иначе карты не дойдут.",
            show_alert=True,
        )

    @router.callback_query(F.data == CB_BK_LEAVE)
    async def handle_leave(callback: CallbackQuery) -> None:
        """убирает игрока из лобби до старта партии"""
        session = sessions.get(_chat_id(callback))
        if session is None or session.phase != "lobby":
            await callback.answer()
            return
        player_id = callback.from_user.id
        if session.players.pop(player_id, None) is None:
            await callback.answer("Ты не в лобби.", show_alert=True)
            return
        session.hands.pop(player_id, None)
        session.hands_delivered.discard(player_id)
        session.reachable.discard(player_id)
        await _show_board(callback.bot, session, bot_username)
        await _save_session(session.board_chat_id)
        await callback.answer("Ты покинул лобби.")

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
        await _show_board(callback.bot, session, bot_username)
        await _save_session(session.board_chat_id)
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
            session.hands_delivered = set()
        unreachable = await _deliver_hands(bot, session)
        if unreachable:
            await _save_session(session.board_chat_id)
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
        begin_round(session, 1)
        await send_with_retry(bot, session.board_chat_id, render_intro(session))
        await _show_board(bot, session, bot_username)
        await _save_session(session.board_chat_id)
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
            await send_with_retry(
                bot,
                session.board_chat_id,
                f"🃏 {session.players[player_id]} открывает - "
                f"{BUNKER_CARD_LABELS[key]}: {card}",
            )
        await _show_board(callback.bot, session, bot_username)
        await _save_session(session.board_chat_id)
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
        open_vote(session, alive(session))
        await _show_board(callback.bot, session, bot_username)
        await _save_session(session.board_chat_id)
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
            await callback.answer("Голосуют только активные игроки.", show_alert=True)
            return
        candidate = _parse_int((callback.data or "")[len(CB_BK_VOTE_PREFIX) :])
        if candidate is None or candidate not in session.vote_candidates:
            await callback.answer()
            return

        session.votes[voter_id] = candidate
        if len(session.votes) >= len(alive(session)):
            await _close_vote(callback, session)
        else:
            await _show_board(callback.bot, session, bot_username)
            await _save_session(session.board_chat_id)
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
        begin_round(session, session.round_no + 1)
        await _announce_round(callback, session)
        await _show_board(callback.bot, session, bot_username)
        await _save_session(session.board_chat_id)
        await callback.answer()

    @router.callback_query(F.data == CB_BK_CANCEL)
    async def handle_cancel(callback: CallbackQuery) -> None:
        """создатель или админ чата отменяет партию"""
        chat_id = _chat_id(callback)
        session = sessions.get(chat_id)
        if session is None:
            await callback.answer()
            return
        bot = callback.bot
        allowed = (
            callback.from_user.id == session.host_id
            if bot is None
            else await is_chat_manager(
                bot, chat_id, callback.from_user.id, session.host_id
            )
        )
        if not allowed:
            await callback.answer(
                "Отменить может создатель или админ чата.", show_alert=True
            )
            return
        sessions.pop(session.board_chat_id, None)
        await _replace_board(callback, "Партия в бункер отменена.")
        await _save_session(session.board_chat_id)
        await callback.answer()

    @router.message(Command("joinbunker"))
    async def handle_join_solo(message: Message, command: CommandObject) -> None:
        """присоединяет игрока к режиму «отдельно» по коду"""
        if message.chat.type != "private":
            await message.answer("Команда /joinbunker работает в личке с ботом.")
            return
        if message.from_user is None:
            return
        code = normalize_code(command.args or "")
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

        previous_code = leave_current_lobby(member_id, lobbies, member_lobby)
        lobby.members[member_id] = message.from_user.full_name
        member_lobby[member_id] = code
        await message.answer(f"Ты в убежище. Код {code}. Жди старта от создателя.")
        bot = message.bot
        if bot is not None and lobby.message_id is not None:
            try:
                await bot.edit_message_text(
                    render_solo_lobby(lobby),
                    chat_id=lobby.host_id,
                    message_id=lobby.message_id,
                    reply_markup=create_bunker_solo_lobby_keyboard(),
                )
            except TelegramBadRequest:
                pass
        await _save_lobby(previous_code)
        await _save_lobby(code)

    @router.callback_query(F.data == CB_BK_SOLO_START)
    async def handle_solo_start(callback: CallbackQuery) -> None:
        """раздаёт карты участникам режима «отдельно»"""
        lobby = lookup_lobby(callback.from_user.id, lobbies, member_lobby)
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

        # руки и общий стол фиксируются: повтор «Начать» после недоставки
        # дошлёт те же карты только тем, кому ещё не дошло
        if set(lobby.hands) != set(lobby.members):
            lobby.hands = dict(
                zip(lobby.members, deal_hands(content, count), strict=True)
            )
            lobby.intro = render_solo_intro(
                pick_catastrophe(content),
                pick_pairs(content, ROUNDS_TOTAL),
                rounds_plan(count),
                count,
            )
            lobby.delivered = set()
        unreachable: list[str] = []
        for member_id, name in lobby.members.items():
            if member_id in lobby.delivered:
                continue
            try:
                await bot.send_message(member_id, render_hand(lobby.hands[member_id]))
                await bot.send_message(member_id, lobby.intro)
            except TelegramForbiddenError:
                unreachable.append(name)
                continue
            lobby.delivered.add(member_id)
        if unreachable:
            await _save_lobby(lobby.code)
            await callback.answer(
                "Не дошли карты: " + ", ".join(unreachable) + ". Им нужно "
                "написать боту /start и снова нажать «Начать».",
                show_alert=True,
            )
            return

        code = lobby.code
        lobby.started = True
        drop_lobby(lobby, lobbies, member_lobby)
        await _replace_board(
            callback,
            f"Карты розданы {count} игрокам. Играйте: открывайте карты по "
            "одной каждый раунд и голосуйте за изгнание сами.",
        )
        await _save_lobby(code)
        await callback.answer()

    @router.callback_query(F.data == CB_BK_SOLO_CANCEL)
    async def handle_solo_cancel(callback: CallbackQuery) -> None:
        """создатель закрывает лобби режима «отдельно»"""
        lobby = lookup_lobby(callback.from_user.id, lobbies, member_lobby)
        if lobby is None:
            await callback.answer()
            return
        if callback.from_user.id != lobby.host_id:
            await callback.answer("Закрыть может создатель.", show_alert=True)
            return
        code = lobby.code
        drop_lobby(lobby, lobbies, member_lobby)
        await _replace_board(callback, "Лобби бункера закрыто.")
        await _save_lobby(code)
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
            await _show_board(callback.bot, session, bot_username)
            await _save_session(session.board_chat_id)
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
                await send_with_retry(
                    bot,
                    session.board_chat_id,
                    f"⚖️ Ничья: {names}. Переголосование среди них.",
                )
            session.revote = True
            open_vote(session, leaders)
            await _show_board(bot, session, bot_username)
            await _save_session(session.board_chat_id)
            await callback.answer()
            return

        excluded_id = leaders[0] if len(leaders) == 1 else random.choice(leaders)
        exclude_player(session, excluded_id)
        if bot is not None:
            await send_with_retry(
                bot, session.board_chat_id, render_exclusion(session, excluded_id)
            )
        session.votes_pending -= 1

        if session.votes_pending > 0:
            open_vote(session, alive(session))
            await _show_board(bot, session, bot_username)
            await _save_session(session.board_chat_id)
            await callback.answer()
            return
        if session.round_no >= ROUNDS_TOTAL:
            await _finale(callback, session)
            return
        begin_round(session, session.round_no + 1)
        await _announce_round(callback, session)
        await _show_board(bot, session, bot_username)
        await _save_session(session.board_chat_id)
        await callback.answer()

    async def _announce_round(callback: CallbackQuery, session: BunkerSession) -> None:
        """публикует исследование бункера нового раунда"""
        bot = callback.bot
        if bot is not None:
            await send_with_retry(
                bot, session.board_chat_id, render_pair(session, session.round_no)
            )

    async def _finale(callback: CallbackQuery, session: BunkerSession) -> None:
        """завершает партию: базовый итог либо история выживания"""
        if session.story_mode:
            await _start_story(callback, session)
            return
        bot = callback.bot
        if bot is not None:
            await send_with_retry(bot, session.board_chat_id, render_finale(session))
        sessions.pop(session.board_chat_id, None)
        await _replace_board(callback, "Бункер закрыт. Игра окончена.")
        await _save_session(session.board_chat_id)
        await callback.answer()

    async def _start_story(callback: CallbackQuery, session: BunkerSession) -> None:
        """запускает развязку «история выживания»"""
        session.survivors_bunker = alive(session)
        session.survivors_exiles = list(session.excluded)
        session.finale_queue = build_finale_queue(session, content)
        session.finale_index = 0
        bot = callback.bot
        if bot is not None:
            await send_with_retry(
                bot, session.board_chat_id, render_story_start(session)
            )
        await _present_challenge(callback, session)

    async def _present_challenge(
        callback: CallbackQuery, session: BunkerSession
    ) -> None:
        """показывает следующее испытание или подводит итог истории"""
        while session.finale_index < len(session.finale_queue):
            if challenge_survivors(session, session.finale_queue[session.finale_index]):
                break
            session.finale_index += 1
        if session.finale_index >= len(session.finale_queue):
            await _story_verdict(callback, session)
            return
        session.phase = "story"
        session.story_votes = {}
        bot = callback.bot
        if bot is not None:
            await send_with_retry(bot, session.board_chat_id, render_challenge(session))
        await _show_board(bot, session, bot_username)
        await _save_session(session.board_chat_id)
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
            await send_with_retry(
                bot, session.board_chat_id, render_outcome(challenge, survived)
            )
        if not survived:
            casualty = apply_casualty(session, challenge)
            if bot is not None:
                await send_with_retry(bot, session.board_chat_id, casualty)
        session.finale_index += 1
        await _present_challenge(callback, session)

    async def _story_verdict(callback: CallbackQuery, session: BunkerSession) -> None:
        """объявляет, кто пережил историю выживания"""
        bot = callback.bot
        if bot is not None:
            await send_with_retry(
                bot, session.board_chat_id, render_story_verdict(session)
            )
        sessions.pop(session.board_chat_id, None)
        await _replace_board(callback, "История выживания завершена.")
        await _save_session(session.board_chat_id)
        await callback.answer()

    return router


async def restore_bunker_sessions(
    storage: SQLiteHistoryStorage,
    sessions: dict[int, BunkerSession],
    lobbies: dict[str, SoloLobby],
    member_lobby: dict[int, str],
) -> None:
    """наполняет партии, лобби и индекс участников из хранилища при старте"""
    raw_sessions = await storage.load_session_scope(_SCOPE)
    for key, data in raw_sessions.items():
        try:
            sessions[int(key)] = load_session(json.loads(data))
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            # снапшот несовместимой/повреждённой схемы - пропускаем
            logger.exception(
                "session_restore_failed",
                extra={"scope": _SCOPE, "key": key},
            )
    raw_lobbies = await storage.load_session_scope(_LOBBY_SCOPE)
    for key, data in raw_lobbies.items():
        try:
            lobby = load_lobby(json.loads(data))
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            logger.exception(
                "session_restore_failed",
                extra={"scope": _LOBBY_SCOPE, "key": key},
            )
            continue
        lobbies[lobby.code] = lobby
        for member_id in lobby.members:
            member_lobby[member_id] = lobby.code


async def _deliver_hands(bot: Bot, session: BunkerSession) -> list[str]:
    """рассылает карты тем, кому ещё не дошло, возвращает имена недоступных"""
    unreachable: list[str] = []
    for player_id, hand in session.hands.items():
        if player_id in session.hands_delivered:
            continue
        try:
            await bot.send_message(player_id, render_hand(hand))
        except TelegramForbiddenError:
            session.reachable.discard(player_id)
            unreachable.append(session.players[player_id])
            continue
        session.hands_delivered.add(player_id)
        session.reachable.add(player_id)
    return unreachable


async def _probe_private_chat(bot: Bot | None, player_id: int) -> bool:
    """проверяет, дойдут ли карты игроку, коротким сообщением в личку"""
    if bot is None:
        return False
    try:
        await bot.send_message(
            player_id, "Ты в лобби «Бункера». Карты придут сюда после старта."
        )
    except TelegramForbiddenError:
        return False
    return True


async def _show_board(
    bot: Bot | None, session: BunkerSession, bot_username: str
) -> None:
    """правит существующее табло, публикует новое только если это не вышло

    старые табло с живыми кнопками копились бы в чате и путали игроков,
    поэтому на смене фазы правится одно и то же сообщение
    """
    if bot is None:
        return
    text = render_board(session)
    keyboard = board_keyboard(session, bot_username)
    if session.board_message_id is not None:
        try:
            await bot.edit_message_text(
                text,
                chat_id=session.board_chat_id,
                message_id=session.board_message_id,
                reply_markup=keyboard,
            )
            return
        except TelegramBadRequest:
            # сообщение удалено или слишком старое для правки - публикуем новое
            pass
    sent = await send_with_retry(bot, session.board_chat_id, text, keyboard)
    if sent is not None:
        session.board_message_id = sent.message_id


async def _replace_board(callback: CallbackQuery, text: str) -> None:
    """заменяет сообщение-табло финальным текстом без клавиатуры"""
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text)
        except TelegramBadRequest:
            pass


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
