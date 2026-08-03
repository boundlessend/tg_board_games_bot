"""тексты и клавиатуры табло игры бункер"""

from aiogram.types import InlineKeyboardMarkup

from constants import BUNKER_CARD_LABELS
from keyboards import (
    create_bunker_lobby_keyboard,
    create_bunker_reveal_keyboard,
    create_bunker_story_keyboard,
    create_bunker_vote_keyboard,
)
from services.bunker import MAX_PLAYERS, MIN_PLAYERS, PlayerHand, RoundsPlan
from services.bunker_state import (
    ROUNDS_TOTAL,
    BunkerSession,
    Challenge,
    SoloLobby,
    alive,
    challenge_survivors,
    story_survivors,
)


def board_keyboard(session: BunkerSession, bot_username: str) -> InlineKeyboardMarkup:
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
    return create_bunker_lobby_keyboard(session.story_mode, bot_username)


def render_board(session: BunkerSession) -> str:
    """отображает табло партии под текущую фазу"""
    if session.phase == "lobby":
        return render_lobby(session)
    if session.phase == "story":
        return render_story_board(session)

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
        players_alive = alive(session)
        opened = [
            p
            for p in players_alive
            if session.revealed_count.get(p, 0) >= session.round_no
        ]
        lines.append(f"Откройте по карте. Открыли: {len(opened)}/{len(players_alive)}")
        lines.append(
            "Дальше - голосование за изгнание."
            if session.votes_pending > 0
            else "Голосования в этом раунде нет."
        )
    elif session.phase == "vote":
        scope = " (переголосование)" if session.revote else ""
        lines.append(
            f"Голосование за изгнание{scope}. "
            f"Проголосовали: {len(session.votes)}/{len(alive(session))}."
        )
        lines.append("Жмите кандидата - голос тайный.")
    return "\n".join(lines)


def render_lobby(session: BunkerSession) -> str:
    """отображает лобби сбора игроков с отметкой доступности лички"""
    mode = "история выживания" if session.story_mode else "базовый"
    lines = [
        "🏚 Бункер. Сбор в убежище.",
        f"Режим: {mode}.",
        "",
        "Игроки:",
    ]
    if session.players:
        for player_id, name in session.players.items():
            mark = "✅" if player_id in session.reachable else "⚠️"
            lines.append(f"{mark} {name}")
    else:
        lines.append("- пока никого")
    unreachable = [
        name
        for player_id, name in session.players.items()
        if player_id not in session.reachable
    ]
    lines.extend(
        [
            "",
            f"Нужно {MIN_PLAYERS}-{MAX_PLAYERS} игроков. Карты придут в личку.",
        ]
    )
    if unreachable:
        lines.append(
            "⚠️ не открыли ЛС с ботом: "
            + ", ".join(unreachable)
            + ". Им нужно нажать /start в личке и вступить заново."
        )
    lines.append("Создатель жмёт «Начать».")
    return "\n".join(lines)


def render_intro(session: BunkerSession) -> str:
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
        f"{render_pair(session, 1)}"
    )


def render_pair(session: BunkerSession, round_no: int) -> str:
    """отображает пару карт бункера и угрозы данного раунда"""
    item, threat = session.pairs[round_no - 1]
    return (
        f"📦 Исследование бункера (раунд {round_no})\n"
        f"Бункер: {item}\n"
        f"⚠️ Угроза: {threat}"
    )


def render_hand(hand: PlayerHand) -> str:
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


def render_exclusion(session: BunkerSession, player_id: int) -> str:
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


def render_finale(session: BunkerSession) -> str:
    """отображает финал базового режима: состав бункера"""
    survivors = alive(session)
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


def render_story_start(session: BunkerSession) -> str:
    """отображает старт истории выживания: состав групп"""
    bunker = ", ".join(session.players[pid] for pid in session.survivors_bunker)
    exiles = ", ".join(session.players[pid] for pid in session.survivors_exiles)
    return "\n".join(
        [
            "🎬 История выживания",
            f"В бункере: {bunker or 'никого'}.",
            f"Снаружи (изгнанные): {exiles or 'никого'}.",
            "Проверим, кто переживёт угрозы и катастрофу. Голосуют все.",
        ]
    )


def render_challenge(session: BunkerSession) -> str:
    """отображает объявление нового испытания"""
    challenge = session.finale_queue[session.finale_index]
    icon = "☢️" if challenge.kind == "catastrophe" else "⚠️"
    names = ", ".join(
        session.players[pid] for pid in challenge_survivors(session, challenge)
    )
    return "\n".join(
        [
            f"{icon} {challenge_header(challenge)}",
            challenge.text,
            "",
            f"Под угрозой: {names}.",
            "Голосуйте: хватит ли у группы трёх полезных карт?",
        ]
    )


def render_story_board(session: BunkerSession) -> str:
    """отображает табло текущего испытания истории выживания"""
    challenge = session.finale_queue[session.finale_index]
    return "\n".join(
        [
            "🎬 История выживания",
            f"{challenge_header(challenge)}: {challenge.text}",
            f"Проголосовали: {len(session.story_votes)}/{len(session.players)}.",
            "Жмите «Справились» или «Не справились».",
        ]
    )


def render_outcome(challenge: Challenge, survived: bool) -> str:
    """отображает исход голосования по испытанию"""
    header = challenge_header(challenge)
    if survived:
        return f"✅ {header}: группа справилась с «{challenge.text}»."
    return f"❌ {header}: не справились с «{challenge.text}»."


def render_story_verdict(session: BunkerSession) -> str:
    """отображает итог истории выживания"""
    survivors = story_survivors(session)
    if survivors:
        names = ", ".join(session.players[pid] for pid in survivors)
        return f"🏆 ИТОГ ИСТОРИИ ВЫЖИВАНИЯ\nВыжили: {names}. Поздравляем!"
    return "☠️ ИТОГ ИСТОРИИ ВЫЖИВАНИЯ\nНе выжил никто."


def render_solo_lobby(lobby: SoloLobby) -> str:
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


def render_solo_intro(
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
        f"Игроков: {count}. Мест в бункере: {plan.seats}. Изгнать: {plan.exclusions}.",
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


def challenge_header(challenge: Challenge) -> str:
    """возвращает заголовок испытания по его группе"""
    return {
        "bunker": "Угроза в бункере",
        "exiles": "Угроза изгнанным",
        "all": "Финальная катастрофа",
    }[challenge.group]
