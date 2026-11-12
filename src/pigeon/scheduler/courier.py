"""Independent morning fortune courier: no game events, at most four per local week."""

from __future__ import annotations

import logging
from datetime import timedelta

from aiogram.exceptions import TelegramForbiddenError, TelegramMigrateToChat

from pigeon.budget import limits
from pigeon.services import fortune_decks

log = logging.getLogger("pigeon.courier")
WEEKLY_LIMIT = 4
END_HOUR = 14


def _window(chat, local_now):
    start = local_now.replace(hour=chat.quiet_end, minute=0, second=0, microsecond=0)
    end = local_now.replace(hour=min(END_HOUR, chat.quiet_start), minute=0, second=0, microsecond=0)
    return max(start, local_now), end


def _week(local_now):
    start = local_now.date() - timedelta(days=local_now.weekday())
    return start.isoformat(), (start + timedelta(days=7)).isoformat()


async def _tick_chat(store, clock, rng, sender, fortunes, names, chat):
    # Another chat's send awaited the network; commands may have changed these settings.
    chat = store.get_chat(chat.chat_id)
    if chat is None or not store.is_enabled(chat.chat_id):
        return
    now = clock.now()
    local_now = limits.chat_local(chat, now)
    day = local_now.date().isoformat()
    week, next_week = _week(local_now)
    if store.courier_published(chat.chat_id, day):
        return
    remaining = WEEKLY_LIMIT - store.courier_count(chat.chat_id, week, next_week)
    plan = store.courier_plan(chat.chat_id, day)
    if plan is None:
        start, end = _window(chat, local_now)
        planned = None
        if remaining > 0 and start < end and rng.random() < remaining / (7 - local_now.weekday()):
            # Leave a full minute for the 30-second scheduler to consume a late plan.
            # In the last partial minute the only safe choice is to fire immediately.
            minutes = int((end - start).total_seconds() // 60)
            planned = start + timedelta(minutes=rng.randrange(minutes) if minutes else 0)
        store.save_courier_plan(chat.chat_id, day, planned, "pending" if planned else "skipped")
        plan = store.courier_plan(chat.chat_id, day)
    if plan.status != "pending" or plan.planned_at is None or now < plan.planned_at:
        return
    if remaining <= 0 or not limits.can_publish(chat, now) or local_now.hour >= END_HOUR:
        store.save_courier_plan(chat.chat_id, day, plan.planned_at, "skipped")
        return
    pending = fortune_decks.prepare_courier(store, chat.chat_id, day, fortunes, names, rng)
    # SQLite/rendering can cross the deadline even without an await.
    chat = store.get_chat(chat.chat_id)
    enabled = chat is not None and store.is_enabled(chat.chat_id)
    send_now = clock.now()
    if (
        not enabled
        or not limits.can_publish(chat, send_now)
        or limits.chat_local(chat, send_now).hour >= END_HOUR
        or limits.chat_local(chat, send_now).date().isoformat() != day
    ):
        store.save_courier_plan(pending["chat_id"], day, plan.planned_at, "skipped")
        return
    await sender.send_text(chat.chat_id, pending["rendered_text"])
    acknowledged_at = clock.now()
    published_day = limits.chat_local(chat, acknowledged_at).date().isoformat()
    store.confirm_courier(chat.chat_id, day, published_day, pending["id"], acknowledged_at)


async def tick(store, clock, rng, sender, fortunes, names=("Гена",)):
    fortune_decks.migrate_courier(store, fortunes)
    for chat in store.enabled_chats():
        try:
            await _tick_chat(store, clock, rng, sender, fortunes, names, chat)
        except TelegramForbiddenError:
            store.disable_chat(chat.chat_id)
            log.warning("chat %s: Telegram access forbidden; chat disabled", chat.chat_id)
        except TelegramMigrateToChat as exc:
            store.migrate_chat(chat.chat_id, exc.migrate_to_chat_id)
        except Exception:
            log.exception("chat %s: fortune courier failed", chat.chat_id)
