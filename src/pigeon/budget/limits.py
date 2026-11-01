"""Limits on spontaneous (automatic) arrivals: budget, quiet hours, live-chat check.

All local times are chat-local. Storage and comparisons use aware datetimes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pigeon.repo.store import Store, minute_key

DAILY_AUTO_LIMIT = 1
WEEKLY_AUTO_LIMIT = 3

# Live-chat check: if at least LIVE_THRESHOLD messages arrived in the last LIVE_WINDOW,
# the arrival is postponed by POSTPONE_MINUTES, at most MAX_POSTPONES times.
LIVE_WINDOW = timedelta(minutes=10)
LIVE_THRESHOLD = 5
POSTPONE = timedelta(minutes=15)
MAX_POSTPONES = 2


def local_day_start_utc(local_now: datetime) -> datetime:
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC)


def local_week_start_utc(local_now: datetime) -> datetime:
    monday = local_now - timedelta(days=local_now.weekday())
    start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC)


def can_plan_auto(store: Store, chat_id: int, local_now: datetime) -> bool:
    """True if one more automatic arrival fits in today's and this week's budget."""
    day_count = store.count_auto_events_since(chat_id, local_day_start_utc(local_now))
    if day_count >= DAILY_AUTO_LIMIT:
        return False
    week_count = store.count_auto_events_since(chat_id, local_week_start_utc(local_now))
    return week_count < WEEKLY_AUTO_LIMIT


def plan_window(local_now: datetime, quiet_start: int, quiet_end: int) -> tuple[datetime, datetime]:
    """The local interval [start, end) for today's arrival: from quiet_end to quiet_start.

    Quiet hours are the complement, so the window needs quiet_end < quiet_start (validated
    when the admin sets them). The start is never earlier than now, so a late plan cannot land
    in the past.
    """
    start = local_now.replace(hour=quiet_end, minute=0, second=0, microsecond=0)
    end = local_now.replace(hour=quiet_start, minute=0, second=0, microsecond=0)
    return max(start, local_now), end


def is_live_chat(store: Store, chat_id: int, now: datetime) -> bool:
    """True if the chat is busy enough that a spontaneous arrival would interrupt it."""
    since = minute_key(now - LIVE_WINDOW)
    return store.messages_since(chat_id, since) >= LIVE_THRESHOLD
