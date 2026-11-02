"""Which days of the week the pigeon flies on, driven through the real scheduler tick."""

import random
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from pigeon.budget import limits
from pigeon.clock import FixedClock
from pigeon.repo.store import Store, minute_key
from pigeon.scheduler import arrivals
from rigged import EveryDay

CHAT = -9101
TZ = "Europe/Moscow"
MONDAY = datetime(2026, 11, 2, 5, 0, tzinfo=UTC)  # 08:00 Moscow


class FakeSender:
    async def send_arrival(self, chat_id, event) -> int:
        return 1

    async def mark_flown(self, chat_id, message_id, count) -> None:
        pass


def _store() -> Store:
    store = Store(":memory:")
    store.enable_chat(CHAT, MONDAY, TZ)
    return store


async def _simulate(store, rng, monday, busy_days=()):
    """Run one week day by day. Returns the weekdays (0 = Monday) with an arrival."""
    flown = []
    sender = FakeSender()
    for day in range(7):
        morning = monday + timedelta(days=day)
        clock = FixedClock(morning)
        await arrivals.tick(store, clock, rng, sender)
        plan = store.get_plan(CHAT, morning.date().isoformat())
        if plan.status != "pending":
            continue
        clock.set(plan.planned_at)
        if day in busy_days:
            store.save_plan(CHAT, plan.local_date, plan.planned_at, "skipped")
            continue
        await arrivals.tick(store, clock, rng, sender)
        assert store.get_plan(CHAT, plan.local_date).status == "fired"
        flown.append(day)
        clock.set(plan.planned_at + timedelta(minutes=61))
        await arrivals.tick(store, clock, rng, sender)
    return flown


async def test_every_week_gets_the_full_budget_on_days_spread_over_it() -> None:
    store = _store()
    rng = random.Random(2026)
    weeks = 700
    days = Counter()
    sets = set()
    for week in range(weeks):
        flown = await _simulate(store, rng, MONDAY + timedelta(weeks=week))
        assert len(flown) == limits.WEEKLY_AUTO_LIMIT
        days.update(flown)
        sets.add(tuple(flown))
    store.close()
    expected = weeks * limits.WEEKLY_AUTO_LIMIT / 7  # 300 per weekday
    assert all(abs(days[d] - expected) < 0.2 * expected for d in range(7)), days
    assert len(sets) == 35  # every way to pick 3 days of 7 occurred


async def test_second_half_of_the_week_is_not_left_empty() -> None:
    store = _store()
    rng = random.Random(1)
    late = 0
    for week in range(200):
        late += any(d >= 3 for d in await _simulate(store, rng, MONDAY + timedelta(weeks=week)))
    store.close()
    assert late > 190  # only Mon+Tue+Wed misses it, 1 week in 35


async def test_lost_arrival_moves_to_the_remaining_days() -> None:
    store = _store()
    # Picks Monday, then a busy chat loses it; the budget must still be spent this week.
    flown = await _simulate(store, EveryDay(3), MONDAY, busy_days={0})
    store.close()
    assert flown == [1, 2, 3]


@pytest.mark.parametrize("weekday,used,certain", [(4, 0, True), (6, 2, True), (5, 1, True)])
def test_last_days_are_certain_when_budget_is_unused(weekday, used, certain) -> None:
    store = _store()
    today = MONDAY + timedelta(days=weekday)
    for i in range(used):
        opened = MONDAY + timedelta(hours=i)
        store.open_event(CHAT, opened, opened + timedelta(minutes=60), "auto")

    class AlmostOne(random.Random):
        def random(self) -> float:
            return 0.999999

    assert limits.flies_today(store, AlmostOne(), CHAT, today.astimezone(limits.UTC)) is certain
    store.close()


def test_no_flight_once_the_budget_is_spent() -> None:
    store = _store()
    for i in range(limits.WEEKLY_AUTO_LIMIT):
        opened = MONDAY + timedelta(days=i)
        store.open_event(CHAT, opened, opened + timedelta(minutes=60), "auto")
    assert limits.flies_today(store, EveryDay(1), CHAT, MONDAY + timedelta(days=6)) is False
    store.close()


async def test_busy_chat_postponement_still_works_on_a_picked_day() -> None:
    store = _store()
    clock = FixedClock(MONDAY)
    await arrivals.tick(store, clock, EveryDay(5), FakeSender())
    plan = store.get_plan(CHAT, "2026-11-02")
    clock.set(plan.planned_at)
    for _ in range(limits.LIVE_THRESHOLD):
        store.record_message(CHAT, minute_key(clock.now()))
    await arrivals.tick(store, clock, EveryDay(5), FakeSender())
    assert store.get_plan(CHAT, "2026-11-02").postpones == 1
    store.close()
