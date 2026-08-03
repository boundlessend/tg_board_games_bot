"""состояние партии в бункер: модели, чистые операции и сериализация

логика здесь не знает про telegram: роутер только вызывает эти функции и
рассылает сообщения по их результатам
"""

import random
from dataclasses import asdict, dataclass, field
from typing import Any

from services.bunker import REVEAL_ORDER, BunkerContent, PlayerHand, RoundsPlan

ROUNDS_TOTAL = 5

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 6
_CODE_ATTEMPTS = 100


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
    hands_delivered: set[int] = field(default_factory=set)
    reachable: set[int] = field(default_factory=set)


@dataclass
class SoloLobby:
    """лёгкое лобби режима «отдельно»: раздаёт карты в личку по коду"""

    host_id: int
    code: str
    message_id: int | None = None
    started: bool = False
    members: dict[int, str] = field(default_factory=dict)
    hands: dict[int, PlayerHand] = field(default_factory=dict)
    intro: str = ""
    delivered: set[int] = field(default_factory=set)


def begin_round(session: BunkerSession, round_no: int) -> None:
    """открывает новый раунд: пара бункер+угроза и план голосований"""
    plan = session.plan
    session.round_no = round_no
    session.phase = "reveal"
    session.votes_pending = plan.votes_per_round[round_no - 1] if plan else 0
    session.votes = {}
    session.vote_candidates = []
    session.revote = False


def open_vote(session: BunkerSession, candidates: list[int]) -> None:
    """начинает голосование среди заданных кандидатов"""
    session.phase = "vote"
    session.votes = {}
    session.vote_candidates = candidates


def exclude_player(session: BunkerSession, player_id: int) -> None:
    """изгоняет игрока и раскрывает все его карты персонажа"""
    session.excluded.add(player_id)
    session.revealed_count[player_id] = len(REVEAL_ORDER)
    session.phase = "reveal"
    session.revote = False


def alive(session: BunkerSession) -> list[int]:
    """возвращает id игроков, не изгнанных из игры"""
    return [pid for pid in session.players if pid not in session.excluded]


def build_finale_queue(
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
    queue.append(Challenge(group="all", kind="catastrophe", text=session.catastrophe))
    return queue


def challenge_survivors(session: BunkerSession, challenge: Challenge) -> list[int]:
    """возвращает живых членов группы данного испытания"""
    if challenge.group == "bunker":
        return session.survivors_bunker
    if challenge.group == "exiles":
        return session.survivors_exiles
    return story_survivors(session)


def story_survivors(session: BunkerSession) -> list[int]:
    """возвращает всех выживших обеих групп"""
    return session.survivors_bunker + session.survivors_exiles


def apply_casualty(session: BunkerSession, challenge: Challenge) -> str:
    """разыгрывает потерю при провале испытания и описывает её"""
    if challenge.kind == "catastrophe":
        victims = story_survivors(session)
        session.survivors_bunker = []
        session.survivors_exiles = []
        names = ", ".join(session.players[pid] for pid in victims)
        return f"☢️ Катастрофа сильнее. Погибли все: {names}."

    is_bunker = challenge.group == "bunker"
    group = session.survivors_bunker if is_bunker else session.survivors_exiles
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


def generate_code(existing: set[str]) -> str:
    """генерирует уникальный код лобби

    алфавит без похожих символов (0/O, 1/I), длина шесть: четырёх цифр было
    мало, чтобы код нельзя было подобрать перебором
    """
    for _ in range(_CODE_ATTEMPTS):
        code = "".join(random.choices(_CODE_ALPHABET, k=_CODE_LENGTH))
        if code not in existing:
            return code
    raise RuntimeError("не удалось сгенерировать код лобби бункера")


def normalize_code(raw_code: str) -> str:
    """приводит введённый код к каноническому виду"""
    return raw_code.strip().upper()


def lookup_lobby(
    user_id: int, lobbies: dict[str, SoloLobby], member_lobby: dict[int, str]
) -> SoloLobby | None:
    """находит лобби режима «отдельно» по участнику"""
    code = member_lobby.get(user_id)
    return lobbies.get(code) if code is not None else None


def drop_lobby(
    lobby: SoloLobby,
    lobbies: dict[str, SoloLobby],
    member_lobby: dict[int, str],
) -> None:
    """удаляет лобби и записи его участников из реестров"""
    for member_id in lobby.members:
        member_lobby.pop(member_id, None)
    lobbies.pop(lobby.code, None)


def leave_current_lobby(
    user_id: int,
    lobbies: dict[str, SoloLobby],
    member_lobby: dict[int, str],
) -> str | None:
    """выводит пользователя из его лобби, возвращает затронутый код

    лобби хоста закрывается целиком; код нужен вызывающему, чтобы точечно
    обновить снапшот именно этого лобби
    """
    lobby = lookup_lobby(user_id, lobbies, member_lobby)
    if lobby is None:
        member_lobby.pop(user_id, None)
        return None
    code = lobby.code
    if lobby.host_id == user_id:
        drop_lobby(lobby, lobbies, member_lobby)
        return code
    lobby.members.pop(user_id, None)
    lobby.hands.pop(user_id, None)
    lobby.delivered.discard(user_id)
    member_lobby.pop(user_id, None)
    return code


def dump_session(session: BunkerSession) -> dict[str, Any]:
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
        "hands": {str(pid): asdict(hand) for pid, hand in session.hands.items()},
        "revealed_count": session.revealed_count,
        "excluded": list(session.excluded),
        "votes": session.votes,
        "vote_candidates": session.vote_candidates,
        "survivors_bunker": session.survivors_bunker,
        "survivors_exiles": session.survivors_exiles,
        "finale_queue": [asdict(ch) for ch in session.finale_queue],
        "finale_index": session.finale_index,
        "story_votes": session.story_votes,
        "hands_delivered": list(session.hands_delivered),
        "reachable": list(session.reachable),
    }


def load_session(data: dict[str, Any]) -> BunkerSession:
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
    session.hands = {int(k): PlayerHand(**hand) for k, hand in data["hands"].items()}
    session.revealed_count = {int(k): v for k, v in data["revealed_count"].items()}
    session.excluded = {int(pid) for pid in data["excluded"]}
    session.votes = {int(k): v for k, v in data["votes"].items()}
    session.vote_candidates = list(data["vote_candidates"])
    session.survivors_bunker = list(data["survivors_bunker"])
    session.survivors_exiles = list(data["survivors_exiles"])
    session.finale_queue = [Challenge(**ch) for ch in data["finale_queue"]]
    session.finale_index = data["finale_index"]
    session.story_votes = {int(k): v for k, v in data["story_votes"].items()}
    session.hands_delivered = {int(pid) for pid in data.get("hands_delivered", [])}
    session.reachable = {int(pid) for pid in data.get("reachable", [])}
    return session


def dump_lobby(lobby: SoloLobby) -> dict[str, Any]:
    """сериализует лобби режима «отдельно»"""
    return {
        "host_id": lobby.host_id,
        "code": lobby.code,
        "message_id": lobby.message_id,
        "started": lobby.started,
        "members": lobby.members,
        "hands": {
            str(member_id): asdict(hand) for member_id, hand in lobby.hands.items()
        },
        "intro": lobby.intro,
        "delivered": list(lobby.delivered),
    }


def load_lobby(data: dict[str, Any]) -> SoloLobby:
    """восстанавливает лобби режима «отдельно» из словаря"""
    lobby = SoloLobby(host_id=data["host_id"], code=data["code"])
    lobby.message_id = data["message_id"]
    lobby.started = data["started"]
    lobby.members = {int(k): v for k, v in data["members"].items()}
    lobby.hands = {
        int(k): PlayerHand(**hand) for k, hand in data.get("hands", {}).items()
    }
    lobby.intro = data.get("intro", "")
    lobby.delivered = {int(pid) for pid in data.get("delivered", [])}
    return lobby


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
