"""M1 completion: trophy, dashboard, quiet hours, live-chat postponement, message counter."""

import random
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pigeon.budget import limits
from pigeon.clock import FixedClock
from pigeon.handlers.pigeon import _quiet_bounds
from pigeon.render import texts
from pigeon.repo.store import Store, minute_key
from pigeon.scheduler import arrivals
from pigeon.services import game

CHAT = -3001
TZ = "Europe/Moscow"  # UTC+3, no DST
MORNING = datetime(2026, 11, 2, 5, 0, tzinfo=UTC)  # 08:00 Moscow


class FakeSender:
    def __init__(self) -> None:
        self.sent = []
        self.flown = []

    async def send_arrival(self, chat_id, event) -> int:
        self.sent.append(event.id)
        return 500 + len(self.sent)

    async def mark_flown(self, chat_id, message_id, count) -> None:
        self.flown.append(message_id)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "pigeon.db")
    yield s
    s.close()


@pytest.fixture
def clock():
    return FixedClock(MORNING)


def _enable(store: Store, chat_id: int = CHAT) -> None:
    store.enable_chat(chat_id, MORNING, TZ)


# trophy


def test_first_hit_on_arrival_takes_the_trophy(store, clock) -> None:
    _enable(store)
    first = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, first.id, 1, "Anna")
    game.hit(store, clock, CHAT, first.id, 2, "Boris")  # second hit does not move it
    assert store.get_trophy(CHAT).holder_user_id == 1


def test_trophy_moves_on_the_next_arrivals_first_hit(store, clock) -> None:
    _enable(store)
    first = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, first.id, 1, "Anna")
    clock.set(first.closes_at)
    second = game.arrive(store, clock, CHAT)
    clock.set(second.opened_at + timedelta(minutes=5))
    game.hit(store, clock, CHAT, second.id, 2, "Boris")
    trophy = store.get_trophy(CHAT)
    assert (trophy.holder_user_id, trophy.holder_name) == (2, "Boris")


def test_same_holder_keeps_trophy_since_date(store, clock) -> None:
    _enable(store)
    first = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, first.id, 1, "Anna")
    since = store.get_trophy(CHAT).since
    clock.set(first.closes_at)
    second = game.arrive(store, clock, CHAT)
    clock.set(second.opened_at + timedelta(minutes=5))
    game.hit(store, clock, CHAT, second.id, 1, "Anna")
    assert store.get_trophy(CHAT).since == since


# dashboard


def test_dashboard_text_shows_trophy_and_weekly_top(store, clock) -> None:
    from pigeon.telegram.dashboard import build_text

    _enable(store)
    event = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, event.id, 1, "Anna")
    text = build_text(store, clock, CHAT)
    assert "Золотой помёт: Anna" in text
    assert "Топ недели: Anna 1" in text
    assert "прилётов: 1" in text


def test_dashboard_is_marked_dirty_by_events(store, clock) -> None:
    _enable(store)
    store.clear_dashboard_dirty(CHAT)
    assert CHAT not in store.dirty_dashboard_chats()
    game.arrive(store, clock, CHAT)
    assert CHAT in store.dirty_dashboard_chats()


async def test_tick_flushes_dirty_dashboards(store, clock) -> None:
    _enable(store)
    game.arrive(store, clock, CHAT)
    refreshed = []

    async def refresh(chat_id: int) -> None:
        refreshed.append(chat_id)

    await arrivals.tick(store, clock, random.Random(1), FakeSender(), refresh)
    assert refreshed == [CHAT]


def test_empty_week_top_text() -> None:
    text = texts.dashboard(arrivals=0, sitting=False, holder=None, holder_days=0, top=[])
    assert "пока никто не попадал" in text
    assert "пока пусто" in text


# quiet hours


def test_quiet_bounds_parsing() -> None:
    assert _quiet_bounds(["23", "9"]) == (23, 9)
    assert _quiet_bounds(["9", "23"]) is None  # 'to' must be earlier than 'from'
    assert _quiet_bounds(["25", "9"]) is None
    assert _quiet_bounds(["23"]) is None
    assert _quiet_bounds(["x", "9"]) is None


async def test_custom_quiet_hours_move_the_plan_window(store, clock) -> None:
    _enable(store)
    store.set_quiet_hours(CHAT, 20, 8)  # daytime window becomes 08:00-20:00
    await arrivals.tick(store, clock, random.Random(3), FakeSender())
    plan = store.get_plan(CHAT, "2026-11-02")
    local = plan.planned_at.astimezone(ZoneInfo(TZ))
    assert 8 <= local.hour < 20


def test_plan_window_respects_quiet_hours() -> None:
    local = datetime(2026, 11, 2, 7, 0, tzinfo=ZoneInfo(TZ))
    start, end = limits.plan_window(local, quiet_start=23, quiet_end=9)
    assert (start.hour, end.hour) == (9, 23)


# live-chat check and postponement


async def test_busy_chat_postpones_then_skips(store, clock) -> None:
    _enable(store)
    await arrivals.tick(store, clock, random.Random(5), FakeSender())
    plan = store.get_plan(CHAT, "2026-11-02")
    sender = FakeSender()

    # Put the chat in a busy state right when the arrival is due.
    clock.set(plan.planned_at)
    for _ in range(limits.LIVE_THRESHOLD):
        store.record_message(CHAT, minute_key(clock.now()))

    await arrivals.tick(store, clock, random.Random(5), sender)
    assert sender.sent == []
    postponed = store.get_plan(CHAT, "2026-11-02")
    assert postponed.status == "pending" and postponed.postpones == 1

    # Still busy at the new time: postpone again, then give up for the day.
    clock.set(postponed.planned_at)
    for _ in range(limits.LIVE_THRESHOLD):
        store.record_message(CHAT, minute_key(clock.now()))
    await arrivals.tick(store, clock, random.Random(5), sender)
    assert store.get_plan(CHAT, "2026-11-02").postpones == 2

    clock.set(store.get_plan(CHAT, "2026-11-02").planned_at)
    for _ in range(limits.LIVE_THRESHOLD):
        store.record_message(CHAT, minute_key(clock.now()))
    await arrivals.tick(store, clock, random.Random(5), sender)
    assert store.get_plan(CHAT, "2026-11-02").status == "skipped"
    assert sender.sent == []


def test_message_counter_keeps_counts_not_text(store) -> None:
    _enable(store)
    now = MORNING
    for _ in range(3):
        store.record_message(CHAT, minute_key(now))
    store.record_message(CHAT, minute_key(now + timedelta(minutes=1)))
    assert store.messages_since(CHAT, minute_key(now)) == 4
    assert store.messages_since(CHAT, minute_key(now + timedelta(minutes=1))) == 1


def test_is_live_chat_uses_a_ten_minute_window(store) -> None:
    _enable(store)
    for _ in range(limits.LIVE_THRESHOLD):
        store.record_message(CHAT, minute_key(MORNING - timedelta(minutes=30)))
    assert not limits.is_live_chat(store, CHAT, MORNING)
    for _ in range(limits.LIVE_THRESHOLD):
        store.record_message(CHAT, minute_key(MORNING))
    assert limits.is_live_chat(store, CHAT, MORNING)
