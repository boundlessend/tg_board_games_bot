"""чистая логика игр: раздача, план раундов, голоса, финал, коды лобби"""

import pytest

from services.bunker import (
    MAX_PLAYERS,
    BunkerContent,
    BunkerContentError,
    deal_hands,
    pick_pairs,
    rounds_plan,
    vote_leaders,
)
from services.bunker_state import (
    BunkerSession,
    Challenge,
    SoloLobby,
    alive,
    apply_casualty,
    begin_round,
    build_finale_queue,
    drop_lobby,
    exclude_player,
    generate_code,
    leave_current_lobby,
    lookup_lobby,
    normalize_code,
    story_survivors,
)
from services.picking import pick_unique, pick_word


def test_hands_are_unique_within_a_game(bunker_content: BunkerContent) -> None:
    """каждому игроку достаётся своя суперсила"""
    hands = deal_hands(bunker_content, MAX_PLAYERS)
    assert len(hands) == MAX_PLAYERS
    superpowers = [hand.superpower for hand in hands]
    assert len(superpowers) == len(set(superpowers))


def test_deal_hands_rejects_impossible_table(bunker_content: BunkerContent) -> None:
    """раздача на нулевой стол - явная ошибка контента, а не пустой список"""
    with pytest.raises(BunkerContentError):
        deal_hands(bunker_content, 0)


def test_rounds_plan_matches_rules() -> None:
    """план раундов совпадает с таблицей правил"""
    plan = rounds_plan(7)
    assert plan.votes_per_round == (0, 1, 1, 1, 1)
    assert plan.exclusions == 4
    assert plan.seats == 3
    with pytest.raises(BunkerContentError):
        rounds_plan(3)


def test_vote_leaders_handles_ties() -> None:
    """лидеры голосования определяются с учётом ничьей"""
    assert vote_leaders({1: 5, 2: 5, 3: 8}) == [5]
    assert set(vote_leaders({1: 5, 2: 8})) == {5, 8}
    assert vote_leaders({}) == []


def test_exclusion_opens_all_cards(bunker_content: BunkerContent) -> None:
    """изгнанный игрок выбывает и его карты считаются раскрытыми"""
    session = BunkerSession(host_id=1, board_chat_id=-100)
    session.players = {1: "Аня", 2: "Боря", 3: "Витя", 4: "Гена"}
    session.plan = rounds_plan(4)
    session.pairs = pick_pairs(bunker_content, 5)
    session.hands = dict(
        zip(session.players, deal_hands(bunker_content, 4), strict=True)
    )
    session.revealed_count = {pid: 0 for pid in session.players}

    begin_round(session, 4)
    assert session.votes_pending == 1
    exclude_player(session, 2)
    assert alive(session) == [1, 3, 4]
    assert session.revealed_count[2] == 5


def test_story_finale_kills_everyone_on_catastrophe(
    bunker_content: BunkerContent,
) -> None:
    """провал катастрофы уносит всех выживших"""
    session = BunkerSession(host_id=1, board_chat_id=-100, story_mode=True)
    session.players = {1: "Аня", 2: "Боря", 3: "Витя", 4: "Гена"}
    session.catastrophe = "падение неба"
    session.pairs = pick_pairs(bunker_content, 5)
    session.survivors_bunker = [1, 2]
    session.survivors_exiles = [3, 4]

    queue = build_finale_queue(session, bunker_content)
    assert [challenge.group for challenge in queue] == [
        "bunker",
        "exiles",
        "exiles",
        "all",
    ]

    threat = Challenge(group="bunker", kind="threat", text="прорыв воды")
    assert "погиб" in apply_casualty(session, threat).lower()

    catastrophe = Challenge(group="all", kind="catastrophe", text="падение неба")
    apply_casualty(session, catastrophe)
    assert story_survivors(session) == []


def test_lobby_codes_are_unique_and_case_insensitive() -> None:
    """код лобби не повторяется и вводится в любом регистре"""
    taken = {generate_code(set()) for _ in range(50)}
    assert len(taken) > 40
    for code in taken:
        assert len(code) == 6
        assert "0" not in code and "O" not in code

    code = generate_code(set())
    assert normalize_code(f"  {code.lower()} ") == code


def test_leaving_lobby_reports_touched_code() -> None:
    """выход из лобби возвращает код, чтобы обновить его снапшот"""
    lobbies: dict[str, SoloLobby] = {}
    member_lobby: dict[int, str] = {}
    lobby = SoloLobby(host_id=1, code="ABC234")
    lobby.members = {1: "Аня", 2: "Боря"}
    lobbies[lobby.code] = lobby
    for member_id in lobby.members:
        member_lobby[member_id] = lobby.code

    assert lookup_lobby(2, lobbies, member_lobby) is lobby
    assert leave_current_lobby(2, lobbies, member_lobby) == "ABC234"
    assert 2 not in lobby.members

    assert leave_current_lobby(1, lobbies, member_lobby) == "ABC234"
    assert lobbies == {} and member_lobby == {}
    assert leave_current_lobby(99, lobbies, member_lobby) is None


def test_drop_lobby_clears_registries() -> None:
    """закрытие лобби чистит оба реестра"""
    lobbies: dict[str, SoloLobby] = {}
    member_lobby: dict[int, str] = {}
    lobby = SoloLobby(host_id=1, code="ZZZ999")
    lobby.members = {1: "Аня"}
    lobbies[lobby.code] = lobby
    member_lobby[1] = lobby.code

    drop_lobby(lobby, lobbies, member_lobby)
    assert lobbies == {} and member_lobby == {}


def test_pick_word_cycles_pool() -> None:
    """слова сессии не повторяются, пока пул не исчерпан"""
    issued: set[str] = set()
    first = pick_word(["a", "b"], issued)
    second = pick_word(["a", "b"], issued)
    assert {first, second} == {"a", "b"}
    # круг замкнулся: выбор снова доступен из полного пула
    assert pick_word(["a", "b"], issued) in {"a", "b"}


def test_pick_unique_on_empty_pool() -> None:
    """пустой пул не выдаёт элемент и не падает"""
    assert pick_unique([], set(), lambda item: str(item)) is None
