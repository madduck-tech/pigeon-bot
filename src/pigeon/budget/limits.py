"""Limits on spontaneous (automatic) arrivals and the hours when the bot plans them.

All times are chat-local. Storage and comparisons use aware datetimes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pigeon.repo.store import Store

DAILY_AUTO_LIMIT = 1
WEEKLY_AUTO_LIMIT = 3

# Automatic arrivals are planned inside this local window; outside it is quiet hours.
PLAN_START_HOUR = 10
PLAN_END_HOUR = 22


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


def plan_window(local_now: datetime) -> tuple[datetime, datetime]:
    """The local interval [start, end) in which today's arrival may be placed.

    The start is never earlier than now, so a plan made late in the day cannot land in the past.
    """
    start = local_now.replace(hour=PLAN_START_HOUR, minute=0, second=0, microsecond=0)
    end = local_now.replace(hour=PLAN_END_HOUR, minute=0, second=0, microsecond=0)
    return max(start, local_now), end
