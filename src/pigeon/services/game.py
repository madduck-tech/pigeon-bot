"""Core mechanics for M1: arrival, hit, leaderboard. No aiogram imports here."""

from __future__ import annotations

from datetime import timedelta
from enum import Enum

from pigeon.clock import Clock
from pigeon.repo.store import Event, Store

ARRIVAL_WINDOW = timedelta(minutes=60)
HIT_POINTS = 1


class NotEnabled(Exception):
    """The chat has not run /pigeon setup yet."""


class HitOutcome(Enum):
    HIT = "hit"
    ALREADY = "already"
    CLOSED = "closed"
    NO_EVENT = "no_event"


def arrive(store: Store, clock: Clock, chat_id: int, source: str = "manual") -> Event | None:
    """Open a new arrival. Returns None if the pigeon is already sitting in this chat.

    source is 'manual' (an admin ran /pigeon arrive) or 'auto' (the scheduler planned it).
    Only 'auto' arrivals count against the spontaneous-message budget.
    """
    if not store.is_enabled(chat_id):
        raise NotEnabled(chat_id)
    now = clock.now()
    current = store.current_open_event(chat_id)
    if current is not None:
        if now < current.closes_at:
            return None
        store.close_event(current.id)
    event_id = store.open_event(chat_id, now, now + ARRIVAL_WINDOW, source=source)
    event = store.get_event(event_id)
    assert event is not None
    return event


def hit(
    store: Store, clock: Clock, chat_id: int, event_id: int, user_id: int, name: str
) -> HitOutcome:
    """Register a hit by a user on an arrival. One hit per user per arrival."""
    event = store.get_event(event_id)
    if event is None or event.chat_id != chat_id:
        return HitOutcome.NO_EVENT
    now = clock.now()
    if event.status != "open" or now >= event.closes_at:
        if event.status == "open":
            store.close_event(event.id)
        return HitOutcome.CLOSED
    store.upsert_member(chat_id, user_id, name)
    if not store.add_hit(chat_id, event.id, user_id, now, HIT_POINTS):
        return HitOutcome.ALREADY
    return HitOutcome.HIT


def close_expired(store: Store, clock: Clock, chat_id: int) -> Event | None:
    """Close the open arrival if its window has passed. Returns the closed event, if any."""
    current = store.current_open_event(chat_id)
    if current is not None and clock.now() >= current.closes_at:
        store.close_event(current.id)
        return current
    return None
