"""Automatic arrivals: one planned arrival per chat per local day, inside the daytime window.

Restart-safe: the plan lives in the database. Each tick re-reads it, so a restart never
double-fires and never loses a day's plan.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from pigeon.budget import limits
from pigeon.clock import Clock
from pigeon.repo.store import ArrivalPlan, ChatSettings, Event, Store
from pigeon.services import game

log = logging.getLogger("pigeon.scheduler")

RefreshDashboard = Callable[[int], Awaitable[None]]


class ArrivalSender(Protocol):
    async def send_arrival(self, chat_id: int, event: Event) -> int:
        """Post an arrival message and return its message_id."""
        ...

    async def mark_flown(self, chat_id: int, message_id: int, count: int) -> None:
        """Edit the arrival message to say the pigeon has flown away."""
        ...


def _plan_today(
    store: Store, rng: random.Random, chat: ChatSettings, local_now: datetime
) -> ArrivalPlan:
    local_date = local_now.date().isoformat()
    tz = local_now.tzinfo
    if not limits.can_plan_auto(store, chat.chat_id, local_now):
        store.save_plan(chat.chat_id, local_date, None, "skipped")
        return ArrivalPlan(chat.chat_id, local_date, None, "skipped")

    start, end = limits.plan_window(local_now, chat.quiet_start, chat.quiet_end)
    minutes_left = int((end - start).total_seconds() // 60)
    if minutes_left <= 0:
        store.save_plan(chat.chat_id, local_date, None, "skipped")
        return ArrivalPlan(chat.chat_id, local_date, None, "skipped")

    offset = rng.randrange(minutes_left)
    planned_at = (start + timedelta(minutes=offset)).astimezone(tz)
    store.save_plan(chat.chat_id, local_date, planned_at, "pending")
    return ArrivalPlan(chat.chat_id, local_date, planned_at, "pending")


async def _tick_chat(
    store: Store,
    clock: Clock,
    rng: random.Random,
    sender: ArrivalSender,
    chat: ChatSettings,
) -> None:
    chat_id = chat.chat_id
    now = clock.now()
    local_now = now.astimezone(ZoneInfo(chat.timezone))

    # 1. Close an arrival whose window has passed, and mark its message as flown.
    event = store.current_open_event(chat_id)
    if event is not None and now >= event.closes_at:
        game.close_event(store, clock, event)
        if event.message_id is not None:
            await sender.mark_flown(chat_id, event.message_id, store.hit_count(event.id))
        event = None

    # 2. Make today's plan once.
    local_date = local_now.date().isoformat()
    plan = store.get_plan(chat_id, local_date) or _plan_today(store, rng, chat, local_now)

    # 3. Fire the plan when its time has come.
    if plan.status != "pending" or plan.planned_at is None or now < plan.planned_at:
        return
    # A manual arrival in progress blocks the planned one for the day.
    if event is not None:
        store.save_plan(chat_id, local_date, plan.planned_at, "skipped")
        return
    # A busy chat postpones the arrival, a limited number of times, then skips the day.
    if limits.is_live_chat(store, chat_id, now):
        _, window_end = limits.plan_window(local_now, chat.quiet_start, chat.quiet_end)
        new_time = now + limits.POSTPONE
        if (
            plan.postpones >= limits.MAX_POSTPONES
            or new_time.astimezone(local_now.tzinfo) >= window_end
        ):
            store.save_plan(chat_id, local_date, plan.planned_at, "skipped")
            log.info("chat %s: busy, arrival skipped for the day", chat_id)
        else:
            store.postpone_plan(chat_id, local_date, new_time.astimezone(local_now.tzinfo))
            log.info("chat %s: busy, arrival postponed", chat_id)
        return

    arrival = game.arrive(store, clock, chat_id, source="auto")
    if arrival is None:
        return
    try:
        message_id = await sender.send_arrival(chat_id, arrival)
    except Exception:
        store.close_event(arrival.id)
        raise
    store.set_event_message(arrival.id, message_id)
    store.save_plan(chat_id, local_date, plan.planned_at, "fired")
    log.info("chat %s: automatic arrival %s", chat_id, arrival.id)


async def tick(
    store: Store,
    clock: Clock,
    rng: random.Random,
    sender: ArrivalSender,
    refresh_dashboard: RefreshDashboard | None = None,
) -> None:
    """Run one pass over every enabled chat. A failure in one chat does not stop the others."""
    for chat in store.enabled_chats():
        try:
            await _tick_chat(store, clock, rng, sender, chat)
        except Exception:
            log.exception("chat %s: scheduler tick failed", chat.chat_id)
    if refresh_dashboard is None:
        return
    for chat_id in store.dirty_dashboard_chats():
        try:
            await refresh_dashboard(chat_id)
        except Exception:
            log.exception("chat %s: dashboard refresh failed", chat_id)
