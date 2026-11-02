from datetime import UTC, datetime, timedelta

import pytest

from pigeon.clock import FixedClock
from pigeon.render import texts
from pigeon.repo.store import Store
from pigeon.services import game, quests

CHAT = -6001
TZ = "Europe/Moscow"
TUESDAY = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
NEXT_MONDAY = datetime(2026, 11, 8, 21, 0, tzinfo=UTC)  # Nov 9, 00:00 Moscow
WEEK = "2026-11-02"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "pigeon.db")
    s.enable_chat(CHAT, TUESDAY, TZ)
    yield s
    s.close()


@pytest.fixture
def clock():
    return FixedClock(TUESDAY)


def _hits(store, clock, count, step=timedelta(hours=2)):
    """Land `count` hits, each on its own arrival, so one user is enough."""
    for _ in range(count):
        event = game.arrive(store, clock, CHAT)
        assert game.hit(store, clock, CHAT, event.id, 1, "Anna") is game.HitOutcome.HIT
        clock.set(event.closes_at)
        game.close_event(store, clock, event)
        clock.set(clock.now() + step - game.ARRIVAL_WINDOW)


def test_target_is_reachable_within_the_weekly_budget() -> None:
    # 3 automatic arrivals a week, 6 members, one hit each (DESIGN §9, §1).
    assert quests.TARGET <= 3 * 6


def test_progress_counts_this_weeks_hits(store, clock) -> None:
    _hits(store, clock, 3)
    assert quests.progress(store, store.get_chat(CHAT), clock.now()) == (3, quests.TARGET)
    assert store.get_pigeon(CHAT).title is None


def test_reaching_the_target_gives_the_pigeon_a_title(store, clock) -> None:
    _hits(store, clock, quests.TARGET)
    pigeon = store.get_pigeon(CHAT)
    key = quests.week_key(clock.now().astimezone(TUESDAY.tzinfo))
    assert pigeon.title == texts.QUEST_TITLE
    assert store.get_quest(CHAT, key).status == "done"


def test_quest_gives_no_points(store, clock) -> None:
    _hits(store, clock, quests.TARGET)
    assert store.balance(CHAT, 1) == quests.TARGET  # only the hits themselves


def test_new_week_starts_from_zero_at_local_monday(store, clock) -> None:
    _hits(store, clock, 2)
    chat = store.get_chat(CHAT)
    assert quests.progress(store, chat, NEXT_MONDAY - timedelta(minutes=1))[0] == 2
    assert quests.progress(store, chat, NEXT_MONDAY)[0] == 0


def test_hit_exactly_at_the_boundary_counts_once_for_the_new_week(store, clock) -> None:
    store.ensure_quest(CHAT, "2026-11-02", quests.TARGET)  # the old week has a quest to report
    clock.set(NEXT_MONDAY)
    _hits(store, clock, 1)
    chat = store.get_chat(CHAT)
    assert quests.progress(store, chat, NEXT_MONDAY)[0] == 1
    assert quests.week_report(store, chat, WEEK) is None  # nothing in the old week


def test_week_report_says_how_it_went(store, clock) -> None:
    _hits(store, clock, 2)
    quests.progress(store, store.get_chat(CHAT), clock.now())
    report = quests.week_report(store, store.get_chat(CHAT), WEEK)
    assert report == texts.QUEST_FAILED.format(hits=2, target=quests.TARGET)


def test_week_report_for_a_done_quest(store, clock) -> None:
    _hits(store, clock, quests.TARGET)
    report = quests.week_report(store, store.get_chat(CHAT), WEEK)
    assert report.startswith("🎯 Квест недели выполнен")


def test_silent_week_has_no_report(store, clock) -> None:
    chat = store.get_chat(CHAT)
    quests.progress(store, chat, clock.now())
    assert quests.week_report(store, chat, WEEK) is None


def test_close_fails_past_quests_but_keeps_done_ones(store, clock) -> None:
    _hits(store, clock, quests.TARGET)
    store.ensure_quest(CHAT, "2026-10-26", quests.TARGET)
    quests.close_week(store, store.get_chat(CHAT), WEEK)
    assert store.get_quest(CHAT, WEEK).status == "done"
    assert store.get_quest(CHAT, "2026-10-26").status == "failed"


def test_week_is_reported_once(store, clock) -> None:
    _hits(store, clock, 2)
    chat = store.get_chat(CHAT)
    assert quests.week_report(store, chat, WEEK) is not None
    quests.close_week(store, chat, WEEK)
    assert quests.week_report(store, chat, WEEK) is None


def test_week_closed_early_can_still_be_completed(store, clock) -> None:
    # A timezone change to the west closes the week while it is still running there.
    _hits(store, clock, quests.TARGET - 1)
    quests.close_week(store, store.get_chat(CHAT), WEEK)
    assert store.get_quest(CHAT, WEEK).status == "failed"
    _hits(store, clock, 1)
    assert store.get_quest(CHAT, WEEK).status == "done"
