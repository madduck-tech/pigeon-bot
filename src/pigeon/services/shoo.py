"""Shooing the pigeon: one attempt per user per arrival, each makes the next likelier to work."""

from __future__ import annotations

import random
from enum import Enum

from pigeon.clock import Clock
from pigeon.content import ShooTexts
from pigeon.repo.store import Store
from pigeon.services import game

BASE_CHANCE = 0.20
CHANCE_STEP = 0.15


class ShooOutcome(Enum):
    STAYED = "stayed"
    FLEW = "flew"
    ALREADY = "already"
    CLOSED = "closed"
    NO_EVENT = "no_event"


def flee_chance(previous_attempts: int) -> float:
    return min(1.0, BASE_CHANCE + CHANCE_STEP * previous_attempts)


def shoo(
    store: Store,
    clock: Clock,
    rng: random.Random,
    lines: ShooTexts,
    chat_id: int,
    event_id: int,
    user_id: int,
    name: str,
) -> ShooOutcome:
    state, event = game.live_event(store, clock, chat_id, event_id)
    if state is game.EventState.NO_EVENT:
        return ShooOutcome.NO_EVENT
    if state is game.EventState.CLOSED or event is None:
        return ShooOutcome.CLOSED
    flew = rng.random() < flee_chance(store.shoo_count(chat_id, event.id))
    reaction = rng.choice(lines.flew if flew else lines.stayed)
    store.upsert_member(chat_id, user_id, name)
    saved = store.add_shoo(
        chat_id, event.id, user_id, rng.choice(lines.actions), reaction, flew, clock.now()
    )
    if not saved:
        return ShooOutcome.ALREADY
    if not flew:
        return ShooOutcome.STAYED
    store.close_event(event.id)
    return ShooOutcome.FLEW
