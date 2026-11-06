"""Automatic arrivals: at most one per chat per local day, on random days of the week.

Restart-safe: the plan lives in the database. Each tick re-reads it, so a restart never
double-fires and never loses a day's plan.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramMigrateToChat

from pigeon.budget import limits
from pigeon.clock import Clock
from pigeon.content import Content
from pigeon.repo.store import ArrivalPlan, ChatSettings, Event, Store
from pigeon.services import achievements, game, weekly

log = logging.getLogger("pigeon.scheduler")


class ArrivalSender(Protocol):
    async def send_arrival(self, chat_id: int, event: Event) -> int:
        """Post an arrival message and return its message_id."""
        ...

    async def mark_flown(self, chat_id: int, message_id: int, text: str) -> None:
        """Replace the arrival message with this text and remove its buttons."""
        ...


def _plan_today(
    store: Store, rng: random.Random, chat: ChatSettings, local_now: datetime
) -> ArrivalPlan:
    local_date = local_now.date().isoformat()
    tz = local_now.tzinfo
    if not limits.flies_today(store, rng, chat.chat_id, local_now):
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
    content: Content,
    chat: ChatSettings,
) -> None:
    chat_id = chat.chat_id
    now = clock.now()
    local_now = now.astimezone(ZoneInfo(chat.timezone))

    # 1. Close an arrival whose window has passed, and mark its message as flown.
    event = store.current_open_event(chat_id)
    if event is not None and now >= event.closes_at:
        store.close_event(event.id)
        if event.message_id is not None:
            await sender.mark_flown(
                chat_id, event.message_id, game.flown_text(store, event, now, content.quests)
            )
        event = None

    # 2. Make today's plan once. The trophy is held with no events at all, so it is checked here.
    local_date = local_now.date().isoformat()
    plan = store.get_plan(chat_id, local_date)
    if plan is None:
        achievements.check_trophy_holders(store, chat_id, now)
        plan = _plan_today(store, rng, chat, local_now)

    # 3. Fire the plan when its time has come.
    if plan.status != "pending" or plan.planned_at is None or now < plan.planned_at:
        return
    # A plan that comes due in quiet hours (after a restart) or during a pause is dropped.
    if not limits.can_publish(chat, now):
        store.save_plan(chat_id, local_date, plan.planned_at, "skipped")
        return
    # A manual arrival in progress blocks the planned one for the day.
    if event is not None:
        store.save_plan(chat_id, local_date, plan.planned_at, "skipped")
        return

    arrival = game.arrive(
        store,
        clock,
        chat_id,
        game.pick_visitor(rng, content),
        source="auto",
        rng=rng,
        dove_names=content.dove_names,
    )
    if arrival is None:
        return
    try:
        message_id = await sender.send_arrival(chat_id, arrival)
    except Exception:
        # Nobody saw it, so it must not count as an arrival. The plan stays and retries.
        if game.delete_unsent_event(store, chat_id, arrival.id):
            raise
        log.warning("chat %s: arrival %s was delivered, response lost", chat_id, arrival.id)
    else:
        store.set_event_message(arrival.id, message_id)
    store.save_plan(chat_id, local_date, plan.planned_at, "fired")
    log.info("chat %s: automatic arrival %s", chat_id, arrival.id)


async def tick(
    store: Store,
    clock: Clock,
    rng: random.Random,
    sender: ArrivalSender,
    content: Content,
) -> None:
    """Run one pass over every enabled chat. A failure in one chat does not stop the others."""
    for chat in store.enabled_chats():
        try:
            weekly.ensure(store, chat.chat_id, clock.now(), content.quests, rng)
            await _tick_chat(store, clock, rng, sender, content, chat)
        except TelegramMigrateToChat as exc:
            # Safety net for a migration whose service message the bot never received.
            log.warning("chat %s moved to %s", chat.chat_id, exc.migrate_to_chat_id)
            store.migrate_chat(chat.chat_id, exc.migrate_to_chat_id)
        except Exception:
            log.exception("chat %s: scheduler tick failed", chat.chat_id)
