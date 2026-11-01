"""Darts on an arrival: one throw per user per local day. Dice value 6 is the bullseye."""

from __future__ import annotations

from enum import Enum
from zoneinfo import ZoneInfo

from pigeon.clock import Clock
from pigeon.repo.store import Store
from pigeon.services import game

DART_POINTS = {6: 3, 5: 2, 4: 1}


class DartOutcome(Enum):
    OK = "ok"
    ALREADY_TODAY = "already_today"
    CLOSED = "closed"
    NO_EVENT = "no_event"


def dart_points(dice_value: int) -> int:
    return DART_POINTS.get(dice_value, 0)


def _local_day(store: Store, clock: Clock, chat_id: int) -> str:
    tz = ZoneInfo(store.get_chat(chat_id).timezone)
    return clock.now().astimezone(tz).date().isoformat()


def check(store: Store, clock: Clock, chat_id: int, event_id: int, user_id: int) -> DartOutcome:
    state, _ = game.live_event(store, clock, chat_id, event_id)
    if state is game.EventState.NO_EVENT:
        return DartOutcome.NO_EVENT
    if state is game.EventState.CLOSED:
        return DartOutcome.CLOSED
    if store.has_thrown_today(chat_id, user_id, _local_day(store, clock, chat_id)):
        return DartOutcome.ALREADY_TODAY
    return DartOutcome.OK


def record(
    store: Store,
    clock: Clock,
    chat_id: int,
    event_id: int,
    user_id: int,
    name: str,
    dice_value: int,
) -> int | None:
    """Store a throw. Returns the points won, or None if the user already threw today."""
    store.upsert_member(chat_id, user_id, name)
    points = dart_points(dice_value)
    saved = store.record_dart(
        chat_id,
        user_id,
        _local_day(store, clock, chat_id),
        event_id,
        dice_value,
        points,
        clock.now(),
    )
    if not saved:
        return None
    store.mark_dashboard_dirty(chat_id)
    return points
