"""A delivered manual game replaces the remaining automatic game for its local day."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pigeon.clock import FixedClock
from pigeon.repo.store import Store
from pigeon.scheduler import arrivals
from pigeon.services import game, shoo
from rigged import CONTENT, EveryDay

CHAT = -2901
BASE = datetime(2026, 11, 2, 6, tzinfo=UTC)  # Moscow 09:00


class Sender:
    def __init__(self):
        self.sent = []

    async def send_arrival(self, chat_id, event):
        self.sent.append((chat_id, event.id))
        return len(self.sent)

    async def mark_flown(self, chat_id, message_id, text):
        pass


@pytest.fixture
def store(tmp_path):
    with_store = Store(tmp_path / "manual.db")
    with_store.enable_chat(CHAT, BASE, "Europe/Moscow")
    yield with_store
    with_store.close()


async def run(store, clock, sender):
    await arrivals.tick(store, clock, EveryDay(29), sender, CONTENT)


def end_manual(store, clock, outcome):
    event = game.arrive(store, clock, CHAT)
    assert event is not None and event.source == "manual"
    store.set_event_message(event.id, 900)
    if outcome == "shooed":
        assert (
            shoo.shoo(store, clock, EveryDay(1), CONTENT.shoos, CHAT, event.id, 1, "Test")
            is shoo.ShooOutcome.FLEW
        )
    else:
        clock.set(event.closes_at)
    return event


@pytest.mark.parametrize("planned", [False, True])
@pytest.mark.parametrize("outcome", ["expired", "shooed"])
async def test_finished_manual_blocks_auto(store, planned, outcome):
    clock, sender = FixedClock(BASE), Sender()
    if planned:
        await run(store, clock, sender)
        assert store.get_plan(CHAT, "2026-11-02").status == "pending"
    event = end_manual(store, clock, outcome)
    clock.set(BASE + timedelta(hours=13))  # 22:00, after any pending plan
    await run(store, clock, sender)
    assert store.get_event(event.id).status == "closed"
    assert sender.sent == []
    assert store.get_plan(CHAT, "2026-11-02").status == "skipped"


async def test_pending_plan_suppressed_after_restart(store):
    clock, sender = FixedClock(BASE), Sender()
    await run(store, clock, sender)
    event = end_manual(store, clock, "shooed")
    path = store._conn.execute("PRAGMA database_list").fetchone()[2]
    reopened = Store(path)
    try:
        assert reopened.get_event(event.id).source == "manual"
        assert reopened.get_plan(CHAT, "2026-11-02").status == "pending"
        clock.set(BASE + timedelta(hours=13))
        await run(reopened, clock, sender)
        await run(reopened, clock, sender)
        assert sender.sent == []
        assert reopened.get_plan(CHAT, "2026-11-02").status == "skipped"
    finally:
        reopened.close()


@pytest.mark.parametrize("planned", [False, True])
@pytest.mark.parametrize("action", [None, "hit", "shoo"])
async def test_failed_send_only_counts_when_participant_proves_delivery(store, planned, action):
    clock, sender = FixedClock(BASE), Sender()
    if planned:
        await run(store, clock, sender)
    event = game.arrive(store, clock, CHAT)
    delivered = action is not None
    if action == "shoo":
        assert (
            shoo.shoo(store, clock, EveryDay(1), CONTENT.shoos, CHAT, event.id, 1, "Test")
            is shoo.ShooOutcome.FLEW
        )
    if action == "hit":
        assert game.hit(store, clock, CHAT, event.id, 1, "Test") is game.HitOutcome.HIT
    assert game.delete_unsent_event(store, CHAT, event.id) is not delivered
    clock.set(BASE + timedelta(hours=13))
    await run(store, clock, sender)
    plan = store.get_plan(CHAT, "2026-11-02")
    # A newly created plan is allowed to be later than the first tick.
    if plan.status == "pending":
        clock.set(plan.planned_at)
        await run(store, clock, sender)
    assert len(sender.sent) == (0 if delivered else 1)
    assert store.get_plan(CHAT, "2026-11-02").status == ("skipped" if delivered else "fired")


async def test_manual_repeats_and_other_chat_remains_independent(store):
    other = CHAT - 1
    store.enable_chat(other, BASE, "Europe/Moscow")
    clock, sender = FixedClock(BASE), Sender()
    await run(store, clock, sender)
    end_manual(store, clock, "shooed")
    repeat = game.arrive(store, clock, CHAT)
    assert repeat is not None
    assert game.arrive(store, clock, CHAT) is None
    clock.set(BASE + timedelta(hours=13))
    await run(store, clock, sender)
    assert [chat for chat, _ in sender.sent] == [other]
    assert store.get_plan(CHAT, "2026-11-02").status == "skipped"


@pytest.mark.parametrize(
    "timezone,local_date",
    [
        ("Europe/Moscow", "2026-11-03"),
        ("America/New_York", "2026-11-01"),  # DST ends: local day lasts 25 hours
        ("America/New_York", "2026-03-08"),  # DST starts: local day lasts 23 hours
    ],
)
@pytest.mark.parametrize("same_day", [False, True])
async def test_local_day_boundary(store, timezone, local_date, same_day):
    zone = ZoneInfo(timezone)
    start = datetime.fromisoformat(local_date).replace(tzinfo=zone)
    # Moscow's same-day premise has the previous UTC date.
    opened = start + timedelta(minutes=1) if same_day else start - timedelta(minutes=1)
    store.enable_chat(CHAT, opened, timezone)
    clock, sender = FixedClock(opened.astimezone(UTC)), Sender()
    end_manual(store, clock, "shooed")
    clock.set((start + timedelta(hours=22)).astimezone(UTC))
    await run(store, clock, sender)
    plan = store.get_plan(CHAT, local_date)
    if plan.status == "pending":
        clock.set(plan.planned_at)
        await run(store, clock, sender)
    assert len(sender.sent) == (0 if same_day else 1)
    assert store.get_plan(CHAT, local_date).status == ("skipped" if same_day else "fired")


async def test_manual_does_not_consume_weekly_auto_budget(store):
    clock, sender = FixedClock(BASE), Sender()
    end_manual(store, clock, "shooed")
    for day in range(7):
        clock.set(BASE + timedelta(days=day))
        await run(store, clock, sender)
        plan = store.get_plan(CHAT, clock.now().date().isoformat())
        if plan.status == "pending":
            clock.set(plan.planned_at)
            await run(store, clock, sender)
            event = store.current_open_event(CHAT)
            clock.set(event.closes_at)
            await run(store, clock, sender)
    assert len(sender.sent) == 3
    assert store.count_auto_events_since(CHAT, BASE) == 3
    assert store.get_plan(CHAT, "2026-11-02").status == "skipped"
