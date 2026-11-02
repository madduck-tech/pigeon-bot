"""Trophy and quiet hours."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pigeon.budget import limits
from pigeon.clock import FixedClock
from pigeon.handlers.pigeon import _quiet_bounds
from pigeon.repo.store import Store
from pigeon.scheduler import arrivals
from pigeon.services import game
from rigged import CONTENT, EveryDay

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

    async def mark_flown(self, chat_id, message_id, text) -> None:
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


# quiet hours


def test_quiet_bounds_parsing() -> None:
    assert _quiet_bounds(["23", "9"]) == (23, 9)
    assert _quiet_bounds(["9", "23"]) is None  # 'to' must be earlier than 'from'
    assert _quiet_bounds(["25", "9"]) is None
    assert _quiet_bounds(["23"]) is None
    assert _quiet_bounds(["x", "9"]) is None
    assert _quiet_bounds(["²", "¹"]) is None  # isdigit() accepts these, int() does not


async def test_custom_quiet_hours_move_the_plan_window(store, clock) -> None:
    _enable(store)
    store.set_quiet_hours(CHAT, 20, 8)  # daytime window becomes 08:00-20:00
    await arrivals.tick(store, clock, EveryDay(3), FakeSender(), CONTENT)
    plan = store.get_plan(CHAT, "2026-11-02")
    local = plan.planned_at.astimezone(ZoneInfo(TZ))
    assert 8 <= local.hour < 20


def test_plan_window_respects_quiet_hours() -> None:
    local = datetime(2026, 11, 2, 7, 0, tzinfo=ZoneInfo(TZ))
    start, end = limits.plan_window(local, quiet_start=23, quiet_end=9)
    assert (start.hour, end.hour) == (9, 23)
