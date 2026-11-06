"""State and transitions for adult arrivals and their single gift."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from pigeon.clock import Clock
from pigeon.repo.store import Event, Store

AREAS = ("cap", "wing", "tail")  # labels live in render.texts
# One gift attempt (download + sendPhoto) is cut off by GIFT_ATTEMPT_DEADLINE. The lease must
# outlive it, so a second tap never starts a parallel attempt while the first may still post.
GIFT_ATTEMPT_DEADLINE = timedelta(seconds=90)
GIFT_LEASE = timedelta(minutes=2)
assert GIFT_LEASE > GIFT_ATTEMPT_DEADLINE


class GiftOutcome(Enum):
    START = "start"
    NO_EVENT = "no_event"
    DISABLED = "disabled"
    NOT_WINNER = "not_winner"
    IN_PROGRESS = "in_progress"
    SENT = "sent"


@dataclass(frozen=True)
class GiftStart:
    outcome: GiftOutcome
    attempt_id: str | None = None


def initial_state(rng: random.Random) -> str:
    return json.dumps({"target": rng.choice(AREAS)})


def _state(event: Event) -> dict:
    value = json.loads(event.type_state)
    if not isinstance(value, dict):
        raise ValueError("adult event state must be an object")
    return value


def target(event: Event) -> str:
    value = _state(event).get("target")
    if value not in AREAS:
        raise ValueError("adult event has no valid target")
    return str(value)


def assign_gift(store: Store, chat_id: int, event_id: int, user_id: int) -> bool:
    event = store.get_event(event_id)
    if event is None or event.chat_id != chat_id or event.pigeon_type != "adult":
        return False
    state = _state(event)
    if state.get("gift_user_id") is not None:
        return False
    state.update({"gift_user_id": user_id, "gift_status": "ready"})
    store.update_event_type_state(chat_id, event_id, json.dumps(state))
    return True


def gift_owner(event: Event) -> int | None:
    if event.pigeon_type != "adult":
        return None
    value = _state(event).get("gift_user_id")
    return int(value) if isinstance(value, int) else None


def gift_visible(event: Event, adult_enabled: bool) -> bool:
    return (
        adult_enabled
        and gift_owner(event) is not None
        and _state(event).get("gift_status") != "sent"
    )


def begin_gift(
    store: Store,
    clock: Clock,
    chat_id: int,
    event_id: int,
    user_id: int,
    attempt_id: str,
) -> GiftStart:
    now = clock.now()
    with store.transaction():
        event = store.get_event(event_id)
        if event is None or event.chat_id != chat_id or event.pigeon_type != "adult":
            return GiftStart(GiftOutcome.NO_EVENT)
        chat = store.get_chat(chat_id)
        if chat is None or not chat.adult_enabled:
            return GiftStart(GiftOutcome.DISABLED)
        state = _state(event)
        if state.get("gift_user_id") != user_id:
            return GiftStart(GiftOutcome.NOT_WINNER)
        status = state.get("gift_status")
        if status == "sent":
            return GiftStart(GiftOutcome.SENT)
        if status == "sending" and not _lease_expired(state.get("gift_started_at"), now):
            return GiftStart(GiftOutcome.IN_PROGRESS)
        state.update(
            {
                "gift_status": "sending",
                "gift_attempt": attempt_id,
                "gift_started_at": now.isoformat(),
            }
        )
        store.update_event_type_state(chat_id, event_id, json.dumps(state))
    return GiftStart(GiftOutcome.START, attempt_id)


def finish_gift(
    store: Store,
    chat_id: int,
    event_id: int,
    attempt_id: str,
    message_id: int | None,
) -> bool:
    with store.transaction():
        event = store.get_event(event_id)
        if event is None or event.chat_id != chat_id or event.pigeon_type != "adult":
            return False
        state = _state(event)
        if state.get("gift_status") != "sending" or state.get("gift_attempt") != attempt_id:
            return False
        if message_id is None:
            state["gift_status"] = "ready"
            state.pop("gift_started_at", None)
        else:
            state["gift_status"] = "sent"
            state["gift_message_id"] = message_id
            state.pop("gift_started_at", None)
        store.update_event_type_state(chat_id, event_id, json.dumps(state))
    return True


def _lease_expired(value, now: datetime) -> bool:
    if not isinstance(value, str):
        return True
    return now - datetime.fromisoformat(value) >= GIFT_LEASE
