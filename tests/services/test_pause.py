from datetime import UTC, datetime, timedelta

import pytest

from pigeon.budget import limits
from pigeon.clock import FixedClock
from pigeon.repo.store import Store
from pigeon.scheduler import arrivals
from pigeon.services import game
from rigged import CONTENT, EveryDay

CHAT = -9001
TZ = "Europe/Moscow"
MORNING = datetime(2026, 11, 2, 5, 0, tzinfo=UTC)  # 08:00 Moscow
NIGHT = datetime(2026, 11, 2, 20, 30, tzinfo=UTC)  # 23:30 Moscow


class FakeSender:
    def __init__(self) -> None:
        self.sent = []
        self.flown = []

    async def send_arrival(self, chat_id, event) -> int:
        self.sent.append(event.id)
        return 1

    async def mark_flown(self, chat_id, message_id, text) -> None:
        self.flown.append(message_id)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "pigeon.db")
    s.enable_chat(CHAT, MORNING, TZ)
    yield s
    s.close()


@pytest.fixture
def clock():
    return FixedClock(MORNING)


async def _plan(store, clock):
    await arrivals.tick(store, clock, EveryDay(5), FakeSender(), CONTENT)
    return store.get_plan(CHAT, "2026-11-02")


async def test_pause_blocks_an_automatic_arrival(store, clock) -> None:
    plan = await _plan(store, clock)
    store.set_paused_until(CHAT, plan.planned_at + timedelta(hours=1))
    clock.set(plan.planned_at)
    sender = FakeSender()
    await arrivals.tick(store, clock, EveryDay(5), sender, CONTENT)
    assert sender.sent == []
    assert store.get_plan(CHAT, "2026-11-02").status == "skipped"


async def test_expired_pause_does_not_block(store, clock) -> None:
    plan = await _plan(store, clock)
    store.set_paused_until(CHAT, plan.planned_at - timedelta(minutes=1))
    clock.set(plan.planned_at)
    sender = FakeSender()
    await arrivals.tick(store, clock, EveryDay(5), sender, CONTENT)
    assert len(sender.sent) == 1


async def test_manual_arrival_works_during_a_pause(store, clock) -> None:
    store.set_paused_until(CHAT, clock.now() + timedelta(days=7))
    assert game.arrive(store, clock, CHAT, source="manual") is not None


async def test_open_arrival_still_closes_during_a_pause(store, clock) -> None:
    event = game.arrive(store, clock, CHAT, source="manual")
    store.set_event_message(event.id, 42)
    store.set_paused_until(CHAT, clock.now() + timedelta(days=7))
    clock.set(event.closes_at)
    sender = FakeSender()
    await arrivals.tick(store, clock, EveryDay(5), sender, CONTENT)
    assert sender.flown == [42]


async def test_overdue_plan_does_not_fire_in_quiet_hours(store, clock) -> None:
    # Regression: after a restart a plan from the evening fired at night.
    await _plan(store, clock)
    clock.set(NIGHT)
    sender = FakeSender()
    await arrivals.tick(store, clock, EveryDay(5), sender, CONTENT)
    assert sender.sent == []


@pytest.mark.parametrize(
    "now,expected",
    [
        (datetime(2026, 11, 2, 12, 0, tzinfo=UTC), False),
        (datetime(2026, 11, 2, 23, 30, tzinfo=UTC), True),
        (datetime(2026, 11, 3, 3, 0, tzinfo=UTC), True),
        (datetime(2026, 11, 3, 9, 0, tzinfo=UTC), False),
    ],
)
def test_is_quiet_wraps_past_midnight(store, now, expected) -> None:
    store.enable_chat(CHAT, MORNING, "UTC")
    assert limits.is_quiet(store.get_chat(CHAT), now) is expected
