"""Limits on what the bot may post by itself: budget, quiet hours, pause.

All local times are chat-local. Storage and comparisons use aware datetimes.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pigeon.repo.store import ChatSettings, Store

DAILY_AUTO_LIMIT = 1
WEEKLY_AUTO_LIMIT = 3
DAYS_IN_WEEK = 7


def chat_local(chat: ChatSettings, now: datetime) -> datetime:
    return now.astimezone(ZoneInfo(chat.timezone))


def is_paused(chat: ChatSettings, now: datetime) -> bool:
    return chat.paused_until is not None and now < chat.paused_until


def is_quiet(chat: ChatSettings, local_now: datetime) -> bool:
    """Quiet hours wrap past midnight: quiet_end < quiet_start is validated on input."""
    return local_now.hour >= chat.quiet_start or local_now.hour < chat.quiet_end


def can_publish(chat: ChatSettings, now: datetime) -> bool:
    """True if the bot may post by itself right now. Every automatic post checks this."""
    return not is_paused(chat, now) and not is_quiet(chat, chat_local(chat, now))


def local_day_start_utc(local_now: datetime) -> datetime:
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC)


def local_week_start_utc(local_now: datetime) -> datetime:
    monday = local_now - timedelta(days=local_now.weekday())
    start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC)


def flies_today(store: Store, rng: random.Random, chat_id: int, local_now: datetime) -> bool:
    """Decide whether today gets an automatic arrival. Called once per day.

    Picking each day with probability (arrivals left / days left) spreads the week's arrivals
    over random days. It needs no stored schedule: a lost arrival stays in the budget and the
    remaining days take it, and the last days are certain if the budget is still unused.
    """
    day_count = store.count_auto_events_since(chat_id, local_day_start_utc(local_now))
    if day_count >= DAILY_AUTO_LIMIT:
        return False
    week_count = store.count_auto_events_since(chat_id, local_week_start_utc(local_now))
    days_left = DAYS_IN_WEEK - local_now.weekday()
    return rng.random() < (WEEKLY_AUTO_LIMIT - week_count) / days_left


def plan_window(local_now: datetime, quiet_start: int, quiet_end: int) -> tuple[datetime, datetime]:
    """The local interval [start, end) for today's arrival: from quiet_end to quiet_start.

    Quiet hours are the complement, so the window needs quiet_end < quiet_start (validated
    when the admin sets them). The start is never earlier than now, so a late plan cannot land
    in the past.
    """
    start = local_now.replace(hour=quiet_end, minute=0, second=0, microsecond=0)
    end = local_now.replace(hour=quiet_start, minute=0, second=0, microsecond=0)
    return max(start, local_now), end
