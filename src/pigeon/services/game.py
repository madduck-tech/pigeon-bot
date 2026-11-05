"""Core mechanics: arrival, hit, trophy, closing. No aiogram imports here."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from pigeon.clock import Clock
from pigeon.content import Content
from pigeon.render import texts
from pigeon.repo.store import Event, Store
from pigeon.services import hit_outcomes, pigeon_types, weekly

ARRIVAL_WINDOW = timedelta(minutes=15)
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


@dataclass(frozen=True)
class Visitor:
    """The pigeon the dovecote releases: a name and the phrase it arrives with."""

    name: str = texts.PIGEON_NAME
    intro: str = texts.ARRIVAL_INTRO


DEFAULT_VISITOR = Visitor()


def pick_visitor(rng: random.Random, content: Content) -> Visitor:
    return Visitor(rng.choice(content.names), rng.choice(content.arrivals))


def arrival_text(store: Store, event: Event, now=None, quests=()) -> str:
    body = texts.arrival(
        event.pigeon_name or texts.PIGEON_NAME,
        event.intro or texts.ARRIVAL_INTRO,
        store.shoos_of(event.chat_id, event.id),
        store.hit_count(event.id),
        event.pigeon_type,
        store.actions_of(event.chat_id, event.id),
    )
    quest = weekly.label(weekly.current(store, event.chat_id, now or event.opened_at, quests))
    return body + ("\n\n" + quest if quest else "")


def flown_text(store: Store, event: Event, now=None, quests=()) -> str:
    body = texts.flown(
        event.pigeon_name or texts.PIGEON_NAME,
        store.shoos_of(event.chat_id, event.id),
        store.hit_count(event.id),
    )
    quest = weekly.label(weekly.current(store, event.chat_id, now or event.opened_at, quests))
    return body + ("\n\n" + quest if quest else "")


def live_event(
    store: Store, clock: Clock, chat_id: int, event_id: int
) -> tuple[EventState, Event | None]:
    """Whether an arrival is still open for this chat. Closes it if its window has passed."""
    event = store.get_event(event_id)
    if event is None or event.chat_id != chat_id:
        return EventState.NO_EVENT, None
    if event.status == "open" and clock.now() >= event.closes_at:
        store.close_event(event.id)
        return EventState.CLOSED, None
    if event.status != "open":
        return EventState.CLOSED, None
    return EventState.OPEN, event


def arrive(
    store: Store,
    clock: Clock,
    chat_id: int,
    visitor: Visitor = DEFAULT_VISITOR,
    source: str = "manual",
    rng: random.Random | None = None,
) -> Event | None:
    """Open a new arrival. Returns None if a pigeon is already sitting in this chat.

    source is 'manual' (an admin ran /pigeon arrive) or 'auto' (the scheduler planned it).
    Only 'auto' arrivals count against the spontaneous-message budget.
    """
    if not store.is_enabled(chat_id):
        raise NotEnabled(chat_id)
    now = clock.now()
    with store.transaction():
        current = store.current_open_event(chat_id)
        if current is not None:
            if now < current.closes_at:
                return None
            store.close_event(current.id)
        pigeon_type = "normal"
        if rng is not None:
            pigeon_type, deck = pigeon_types.draw(rng, store.get_pigeon_deck(chat_id))
            store.save_pigeon_deck(chat_id, deck)
        window = timedelta(minutes=5) if pigeon_type == "golden" else ARRIVAL_WINDOW
        event_id = store.open_event(
            chat_id, now, now + window, source, visitor.name, visitor.intro, pigeon_type
        )
    event = store.get_event(event_id)
    assert event is not None
    return event


def delete_unsent_event(store: Store, chat_id: int, event_id: int) -> bool:
    """Delete an unseen arrival and restore its draw in the same transaction."""
    with store.transaction():
        event = store.get_event(event_id)
        if event is None or event.chat_id != chat_id:
            return False
        deck = store.get_pigeon_deck(chat_id)
        previous = store.previous_pigeon_type(chat_id, event_id)
        if not store.delete_unsent_event(chat_id, event_id):
            return False
        if deck is not None:
            store.save_pigeon_deck(chat_id, pigeon_types.restore(deck, event.pigeon_type, previous))
        return True


def hit(
    store: Store,
    clock: Clock,
    chat_id: int,
    event_id: int,
    user_id: int,
    name: str,
    rng: random.Random | None = None,
    lines: dict[str, tuple[str, ...]] | None = None,
    quests=(),
) -> HitOutcome:
    """Register a hit by a user on an arrival. One hit per user per arrival.

    The first hit on an arrival also takes the trophy (Золотой плевок) for that user.
    """
    state, event = live_event(store, clock, chat_id, event_id)
    if state is EventState.NO_EVENT:
        return HitOutcome.NO_EVENT
    if state is EventState.CLOSED or event is None:
        return HitOutcome.CLOSED
    now = clock.now()
    store.upsert_member(chat_id, user_id, name)
    # Serialize reading the shared cap state with saving the attempt.
    with store.transaction():
        current = store.get_event(event.id)
        assert current is not None
        if rng is not None and lines is not None:
            result = hit_outcomes.roll(rng, lines, current.pigeon_type, current.type_state != "{}")
        else:
            result = hit_outcomes.Result(hit_outcomes.Outcome.NORMAL, 1, "Попал!")
        if not store.add_hit(
            chat_id, event.id, user_id, now, result.points, result.outcome, result.reaction
        ):
            return HitOutcome.ALREADY
    weekly.check(store, chat_id, event_id, now, quests)
    points = result.points
    if points > 0 and store.hit_count(event.id) == 1:
        store.set_trophy(chat_id, user_id, name, now)
    return HitOutcome.HIT
