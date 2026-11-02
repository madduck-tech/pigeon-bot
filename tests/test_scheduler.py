from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pigeon.clock import FixedClock
from pigeon.repo.store import Store
from pigeon.scheduler import arrivals
from pigeon.services import achievements, game
from rigged import CONTENT, EveryDay

CHAT = -2001
TZ = "Europe/Moscow"  # UTC+3, no DST
# 2026-11-02 08:00 Moscow == 05:00 UTC. Before the plan window opens at 10:00 local.
MORNING = datetime(2026, 11, 2, 5, 0, tzinfo=UTC)


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[int, int]] = []
        self.flown: list[tuple[int, int, str]] = []
        self.fail_for: set[int] = set()

    async def send_arrival(self, chat_id: int, event) -> int:
        if chat_id in self.fail_for:
            raise RuntimeError("telegram unavailable")
        self.sent.append((chat_id, event.id))
        return 1000 + len(self.sent)

    async def mark_flown(self, chat_id: int, message_id: int, text: str) -> None:
        self.flown.append((chat_id, message_id, text))


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
    await arrivals.tick(store, clock, rng or EveryDay(7), sender, CONTENT)


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
    assert [flown[:2] for flown in sender.flown] == [(CHAT, 1001)]
    assert "Улетел сам" in sender.flown[0][2]


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


async def test_send_to_a_migrated_chat_moves_it(store, clock, sender) -> None:
    from aiogram.exceptions import TelegramMigrateToChat
    from aiogram.methods import SendMessage

    _enable(store)
    new_chat = -100999

    class MigratedSender(FakeSender):
        async def send_arrival(self, chat_id, event) -> int:
            method = SendMessage(chat_id=chat_id, text="x")
            raise TelegramMigrateToChat(method, "upgraded", new_chat)

    await _run(store, clock, sender)
    plan = store.get_plan(CHAT, "2026-11-02")
    clock.set(plan.planned_at)
    await _run(store, clock, MigratedSender())
    assert store.get_chat(CHAT) is None
    assert store.is_enabled(new_chat)

    # The moved plan is still pending, so the arrival goes out at the new id.
    await _run(store, clock, sender)
    assert [chat_id for chat_id, _ in sender.sent] == [new_chat]


async def test_failed_sends_leave_no_phantom_arrivals(store, clock, sender) -> None:
    # Regression: each failed tick left a closed event that ate the budget and the streaks.
    _enable(store)
    await _run(store, clock, sender)
    plan = store.get_plan(CHAT, "2026-11-02")
    clock.set(plan.planned_at)
    sender.fail_for.add(CHAT)
    for _ in range(5):
        await _run(store, clock, sender)
    assert store.arrivals_total(CHAT) == 0
    assert store.get_plan(CHAT, "2026-11-02").status == "pending"

    sender.fail_for.clear()
    await _run(store, clock, sender)
    assert store.arrivals_total(CHAT) == 1
    assert store.get_plan(CHAT, "2026-11-02").status == "fired"


async def test_delivered_arrival_with_a_lost_response_is_kept(store, clock, sender) -> None:
    # Regression: people tapped it during the send, and a timeout deleted the event under them,
    # leaving a second arrival message.
    _enable(store)
    await _run(store, clock, sender)
    plan = store.get_plan(CHAT, "2026-11-02")
    clock.set(plan.planned_at)

    class TappedThenTimedOut(FakeSender):
        async def send_arrival(self, chat_id, event) -> int:
            assert game.hit(store, clock, chat_id, event.id, 2, "Boris") is game.HitOutcome.HIT
            raise TimeoutError

    await _run(store, clock, TappedThenTimedOut())
    event = store.current_open_event(CHAT)
    assert event is not None and event.message_id is None
    assert store.get_plan(CHAT, "2026-11-02").status == "fired"

    clock.set(event.closes_at)
    await _run(store, clock, sender)
    assert store.current_open_event(CHAT) is None
    assert sender.sent == []


async def test_each_arrival_gets_a_name_and_phrase_from_content(store, clock, sender) -> None:
    _enable(store)
    await _run(store, clock, sender)
    clock.set(store.get_plan(CHAT, "2026-11-02").planned_at)
    await _run(store, clock, sender)
    event = store.current_open_event(CHAT)
    assert event.pigeon_name in CONTENT.names
    assert event.intro in CONTENT.arrivals


async def test_daily_plan_grants_trophy_holder_with_no_events(store, clock, sender) -> None:
    # The trophy is held with nothing happening in the chat, so a quiet week must still count.
    _enable(store)
    store.set_trophy(CHAT, 1, "Anna", MORNING)
    await _run(store, clock, sender)
    assert store.achievements_of(CHAT, 1) == []
    clock.set(MORNING + achievements.TROPHY_HOLDER_TIME)
    await _run(store, clock, sender)
    assert store.achievements_of(CHAT, 1) == [achievements.TROPHY_HOLDER]
