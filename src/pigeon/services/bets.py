"""Bets on an arrival: will it get at least THRESHOLD hits? Fixed stake, settled at close."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pigeon.clock import Clock
from pigeon.repo.store import Store
from pigeon.services import game

STAKE = 1
PAYOUT = 2  # the stake back plus one point
THRESHOLD = 3
CHOICES = ("ge3", "lt3")


class BetOutcome(Enum):
    PLACED = "placed"
    ALREADY = "already"
    CLOSED = "closed"
    NO_EVENT = "no_event"
    NO_BALANCE = "no_balance"


def place(
    store: Store, clock: Clock, chat_id: int, event_id: int, user_id: int, name: str, choice: str
) -> BetOutcome:
    if choice not in CHOICES:
        raise ValueError(choice)
    state, _ = game.live_event(store, clock, chat_id, event_id)
    if state is game.EventState.NO_EVENT:
        return BetOutcome.NO_EVENT
    if state is game.EventState.CLOSED:
        return BetOutcome.CLOSED
    if store.balance(chat_id, user_id) < STAKE:
        return BetOutcome.NO_BALANCE
    store.upsert_member(chat_id, user_id, name)
    if not store.place_bet(chat_id, event_id, user_id, choice, STAKE, clock.now()):
        return BetOutcome.ALREADY
    return BetOutcome.PLACED


def settle(store: Store, event_id: int, chat_id: int, hits: int, now: datetime) -> None:
    """Pay out every open bet on a closed arrival. Losing stakes were already taken."""
    reached = hits >= THRESHOLD
    for user_id, choice in store.open_bets(event_id):
        won = (choice == "ge3") == reached
        if won:
            store.add_points(chat_id, user_id, PAYOUT, "bet_win", event_id, now)
        store.set_bet_status(event_id, user_id, "won" if won else "lost")
