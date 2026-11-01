import random
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pigeon.clock import FixedClock
from pigeon.repo.store import Store
from pigeon.scheduler import arrivals

CHAT = -2001
TZ = "Europe/Moscow"  # UTC+3, no DST
# 2026-11-02 08:00 Moscow == 05:00 UTC. Before the plan window opens at 10:00 local.
MORNING = datetime(2026, 11, 2, 5, 0, tzinfo=UTC)


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[int, int]] = []
        self.flown: list[tuple[int, int, int]] = []
        self.fail_for: set[int] = set()

    async def send_arrival(self, chat_id: int, event) -> int:
        if chat_id in self.fail_for:
            raise RuntimeError("telegram unavailable")
        self.sent.append((chat_id, event.id))
        return 1000 + len(self.sent)

    async def mark_flown(self, chat_id: int, message_id: int, count: int) -> None:
        self.flown.append((chat_id, message_id, count))


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "pigeon.db")
    yield s
    s.close()


@pytest.fixture
def clock():
    return FixedClock(MORNING)


@pytest.fixture
def sender():
    return FakeSender()


def _enable(store: Store, chat_id: int = CHAT, tz: str = TZ) -> None:
    store.enable_chat(chat_id, MORNING, tz)


async def _run(store, clock, sender, rng=None):
    await arrivals.tick(store, clock, rng or random.Random(7), sender)


async def test_nothing_happens_for_chats_not_set_up(store, clock, sender) -> None:
    await _run(store, clock, sender)
    assert sender.sent == []


async def test_plans_once_per_day_inside_daytime_window(store, clock, sender) -> None:
    _enable(store)
    await _run(store, clock, sender)
    plan = store.get_plan(CHAT, "2026-11-02")
    assert plan is not None and plan.status == "pending"
    local = plan.planned_at.astimezone(ZoneInfo(TZ))
    assert 9 <= local.hour < 23  # default quiet hours are 23:00-09:00
    assert sender.sent == []  # not yet due


async def test_fires_at_planned_time_and_counts_as_auto(store, clock, sender) -> None:
    _enable(store)
    await _run(store, clock, sender)
    plan = store.get_plan(CHAT, "2026-11-02")
    clock.set(plan.planned_at)
    await _run(store, clock, sender)
    assert len(sender.sent) == 1
    event = store.current_open_event(CHAT)
    assert event is not None and event.source == "auto"
    assert store.get_plan(CHAT, "2026-11-02").status == "fired"
    assert store.get_event(event.id).message_id == 1001


async def test_one_automatic_arrival_per_day(store, clock, sender) -> None:
    _enable(store)
    await _run(store, clock, sender)
    plan = store.get_plan(CHAT, "2026-11-02")
    clock.set(plan.planned_at)
    await _run(store, clock, sender)
    clock.set(plan.planned_at + timedelta(hours=2))
    await _run(store, clock, sender)
    assert len(sender.sent) == 1


async def test_expired_arrival_is_closed_and_marked_flown(store, clock, sender) -> None:
    _enable(store)
    await _run(store, clock, sender)
    plan = store.get_plan(CHAT, "2026-11-02")
    clock.set(plan.planned_at)
    await _run(store, clock, sender)
    event = store.current_open_event(CHAT)
    clock.set(event.closes_at)
    await _run(store, clock, sender)
    assert store.current_open_event(CHAT) is None
    assert sender.flown == [(CHAT, 1001, 0)]


async def test_manual_arrival_in_progress_blocks_planned_one(store, clock, sender) -> None:
    _enable(store)
    await _run(store, clock, sender)
    plan = store.get_plan(CHAT, "2026-11-02")
    store.open_event(CHAT, plan.planned_at, plan.planned_at + timedelta(minutes=60), "manual")
    clock.set(plan.planned_at)
    await _run(store, clock, sender)
    assert sender.sent == []
    assert store.get_plan(CHAT, "2026-11-02").status == "skipped"


async def test_weekly_budget_caps_automatic_arrivals(store, clock, sender) -> None:
    _enable(store)
    # Three automatic arrivals already happened on Monday and Tuesday of the same week.
    for opened in (
        datetime(2026, 11, 2, 7, 0, tzinfo=UTC),
        datetime(2026, 11, 2, 8, 0, tzinfo=UTC),
        datetime(2026, 11, 3, 7, 0, tzinfo=UTC),
    ):
        store.open_event(CHAT, opened, opened + timedelta(minutes=60), "auto")
    # Wednesday: the weekly cap of 3 is used up, so no plan is made.
    clock.set(datetime(2026, 11, 4, 5, 0, tzinfo=UTC))
    await _run(store, clock, sender)
    assert store.get_plan(CHAT, "2026-11-04").status == "skipped"


async def test_scheduler_is_restart_safe(tmp_path, clock, sender) -> None:
    path = tmp_path / "pigeon.db"
    first = Store(path)
    _enable(first)
    await _run(first, clock, sender)
    plan = first.get_plan(CHAT, "2026-11-02")
    first.close()

    second = Store(path)
    assert second.get_plan(CHAT, "2026-11-02") == plan
    clock.set(plan.planned_at)
    await _run(second, clock, sender)
    await _run(second, clock, sender)
    assert len(sender.sent) == 1
    second.close()


async def test_failure_in_one_chat_does_not_stop_others(store, clock, sender) -> None:
    other = CHAT - 1
    _enable(store, CHAT)
    _enable(store, other)
    await _run(store, clock, sender)
    plan_a = store.get_plan(CHAT, "2026-11-02")
    plan_b = store.get_plan(other, "2026-11-02")
    sender.fail_for.add(CHAT)
    clock.set(max(plan_a.planned_at, plan_b.planned_at))
    await _run(store, clock, sender)
    assert [chat for chat, _ in sender.sent] == [other]
    assert store.current_open_event(CHAT) is None  # the failed arrival was closed again
