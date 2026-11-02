"""Duels: a challenge, then a bout of dart throws. One live duel per chat.

The throwing itself is Telegram work and lives in telegram/duels.py. Here are the rules and the
state. A bout is not resumed after a restart: it stays `active` until its time runs out, then
sweep ends it without points, so a lost bout cannot hold the chat forever.
"""

from __future__ import annotations

from datetime import timedelta
from enum import Enum

from pigeon.clock import Clock
from pigeon.render import texts
from pigeon.repo.store import Duel, Store
from pigeon.services import achievements

ANSWER_TIME = timedelta(minutes=10)
BOUT_TIME = timedelta(minutes=5)
DECISIVE_ROUNDS = 3
MAX_ROUNDS = 9  # a tied round is replayed; this keeps a run of ties from going on forever
WIN_POINTS = 3

Round = tuple[int, int]


class Answer(Enum):
    OK = "ok"
    NOT_OPPONENT = "not_opponent"
    GONE = "gone"


def challenge(
    store: Store, clock: Clock, chat_id: int, challenger_id: int, opponent_id: int
) -> int | None:
    """Create a challenge. Returns its id, or None if the chat already has a live duel."""
    now = clock.now()
    return store.create_duel(chat_id, challenger_id, opponent_id, now, now + ANSWER_TIME)


def member_name(store: Store, chat_id: int, user_id: int) -> str:
    return store.member_name(chat_id, user_id) or "?"


def ended_text(store: Store, duel: Duel) -> str:
    """What the challenge message says after sweep ended the duel."""
    if duel.state == "pending":
        return texts.DUEL_EXPIRED.format(b=member_name(store, duel.chat_id, duel.opponent_id))
    return texts.DUEL_ABORTED


def _answerable(store: Store, clock: Clock, chat_id: int, duel_id: int, user_id: int) -> Answer:
    duel = store.get_duel(chat_id, duel_id)
    if duel is None:
        return Answer.GONE
    if user_id != duel.opponent_id:
        return Answer.NOT_OPPONENT
    if duel.state != "pending" or clock.now() >= duel.expires_at:
        return Answer.GONE
    return Answer.OK


def accept(store: Store, clock: Clock, chat_id: int, duel_id: int, user_id: int) -> Answer:
    answer = _answerable(store, clock, chat_id, duel_id, user_id)
    if answer is not Answer.OK:
        return answer
    started = store.activate_duel(chat_id, duel_id, clock.now() + BOUT_TIME)
    return Answer.OK if started else Answer.GONE


def decline(store: Store, clock: Clock, chat_id: int, duel_id: int, user_id: int) -> Answer:
    answer = _answerable(store, clock, chat_id, duel_id, user_id)
    if answer is not Answer.OK:
        return answer
    return Answer.OK if store.decline_duel(chat_id, duel_id) else Answer.GONE


def score(rounds: list[Round]) -> tuple[int, int]:
    return sum(a > b for a, b in rounds), sum(b > a for a, b in rounds)


def bout_over(rounds: list[Round]) -> bool:
    return sum(score(rounds)) >= DECISIVE_ROUNDS or len(rounds) >= MAX_ROUNDS


def winner_of(duel: Duel, rounds: list[Round]) -> int | None:
    score_a, score_b = score(rounds)
    if score_a == score_b:
        return None
    return duel.challenger_id if score_a > score_b else duel.opponent_id


def finish(store: Store, clock: Clock, duel: Duel, rounds: list[Round]) -> list[str] | None:
    """Record the result and the prize. Returns new achievements, None if the bout was swept."""
    score_a, score_b = score(rounds)
    winner = winner_of(duel, rounds)
    saved = store.finish_duel(
        duel.chat_id, duel.id, len(rounds), score_a, score_b, winner, WIN_POINTS, clock.now()
    )
    if not saved:
        return None
    store.mark_dashboard_dirty(duel.chat_id)
    if winner is None:
        return []
    return achievements.after_duel_win(store, clock, duel.chat_id, winner)


def sweep(store: Store, clock: Clock) -> list[Duel]:
    """End every duel whose time ran out. Returns them as they were, to update their messages."""
    return [
        duel for duel in store.stale_duels(clock.now()) if store.expire_duel(duel.chat_id, duel.id)
    ]
