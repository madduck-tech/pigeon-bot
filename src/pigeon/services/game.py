"""Core mechanics: arrival, hit, trophy, closing. No aiogram imports here."""

from __future__ import annotations

from datetime import timedelta
from enum import Enum

from pigeon.clock import Clock
from pigeon.repo.store import Event, Store
from pigeon.services import bets

ARRIVAL_WINDOW = timedelta(minutes=60)
HIT_POINTS = 1


class NotEnabled(Exception):
    """The chat has not run /pigeon setup yet."""


class HitOutcome(Enum):
    HIT = "hit"
    ALREADY = "already"
    CLOSED = "closed"
    NO_EVENT = "no_event"


class EventState(Enum):
    OPEN = "open"
    CLOSED = "closed"
    NO_EVENT = "no_event"


def close_event(store: Store, clock: Clock, event: Event) -> None:
    """The one place an arrival is closed: settles its bets and refreshes the dashboard."""
    store.close_event(event.id)
    bets.settle(store, event.id, event.chat_id, store.hit_count(event.id), clock.now())
    store.mark_dashboard_dirty(event.chat_id)


def live_event(
    store: Store, clock: Clock, chat_id: int, event_id: int
) -> tuple[EventState, Event | None]:
    """Whether an arrival is still open for this chat. Closes it if its window has passed."""
    event = store.get_event(event_id)
    if event is None or event.chat_id != chat_id:
        return EventState.NO_EVENT, None
    if event.status == "open" and clock.now() >= event.closes_at:
        close_event(store, clock, event)
        return EventState.CLOSED, None
    if event.status != "open":
        return EventState.CLOSED, None
    return EventState.OPEN, event


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
        close_event(store, clock, current)
    event_id = store.open_event(chat_id, now, now + ARRIVAL_WINDOW, source=source)
    store.mark_dashboard_dirty(chat_id)
    event = store.get_event(event_id)
    assert event is not None
    return event


def hit(
    store: Store, clock: Clock, chat_id: int, event_id: int, user_id: int, name: str
) -> HitOutcome:
    """Register a hit by a user on an arrival. One hit per user per arrival.

    The first hit on an arrival also takes the trophy (Золотой помёт) for that user.
    """
    state, event = live_event(store, clock, chat_id, event_id)
    if state is EventState.NO_EVENT:
        return HitOutcome.NO_EVENT
    if state is EventState.CLOSED or event is None:
        return HitOutcome.CLOSED
    now = clock.now()
    store.upsert_member(chat_id, user_id, name)
    if not store.add_hit(chat_id, event.id, user_id, now, HIT_POINTS):
        return HitOutcome.ALREADY
    if store.hit_count(event.id) == 1:
        store.set_trophy(chat_id, user_id, name, now)
    store.mark_dashboard_dirty(chat_id)
    return HitOutcome.HIT
