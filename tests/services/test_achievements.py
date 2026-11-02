from datetime import UTC, datetime, timedelta

import pytest

from pigeon.clock import FixedClock
from pigeon.repo.store import Store
from pigeon.services import achievements, game

CHAT = -5001
TZ = "Europe/Moscow"
START = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "pigeon.db")
    s.enable_chat(CHAT, START, TZ)
    yield s
    s.close()


@pytest.fixture
def clock():
    return FixedClock(START)


def _arrival(store, clock, hitters=()):
    """Open an arrival a day after the last one and hit it with the given users, in order."""
    event = game.arrive(store, clock, CHAT)
    earned = {}
    for user_id in hitters:
        game.hit(store, clock, CHAT, event.id, user_id, f"u{user_id}")
        earned[user_id] = achievements.after_hit(store, clock, CHAT, user_id)
    clock.set(clock.now() + timedelta(days=1))
    return earned


def test_sniper_needs_five_arrivals_in_a_row(store, clock) -> None:
    for _ in range(4):
        assert _arrival(store, clock, [2, 1])[1] == []
    assert achievements.SNIPER in _arrival(store, clock, [2, 1])[1]


def test_sniper_streak_is_broken_by_a_missed_arrival(store, clock) -> None:
    for _ in range(4):
        _arrival(store, clock, [1])
    _arrival(store, clock, [2])  # user 1 misses this one
    for _ in range(4):
        assert achievements.SNIPER not in _arrival(store, clock, [1])[1]
    assert achievements.SNIPER in _arrival(store, clock, [1])[1]


def test_sniper_counts_only_this_chats_arrivals(store, clock) -> None:
    other = -5002
    store.enable_chat(other, START, TZ)
    for _ in range(4):
        _arrival(store, clock, [1])
    event = game.arrive(store, clock, other)  # newer id, other chat
    game.hit(store, clock, other, event.id, 1, "u1")
    clock.set(clock.now() + timedelta(days=1))
    assert achievements.SNIPER in _arrival(store, clock, [1])[1]
    assert store.achievements_of(other, 1) == []


def test_early_bird_counts_only_first_hits(store, clock) -> None:
    for _ in range(4):
        assert achievements.EARLY_BIRD not in _arrival(store, clock, [1, 2])[1]
    last = _arrival(store, clock, [1, 2])
    assert achievements.EARLY_BIRD in last[1]
    assert achievements.EARLY_BIRD not in last[2]


def test_achievement_is_granted_once(store, clock) -> None:
    for _ in range(5):
        _arrival(store, clock, [1])
    assert _arrival(store, clock, [1])[1] == []
    assert store.achievements_of(CHAT, 1).count(achievements.SNIPER) == 1


def test_scarer_comes_with_the_fifth_scared_pigeon(store, clock) -> None:
    for number in range(1, 6):
        event = game.arrive(store, clock, CHAT)
        store.upsert_member(CHAT, 1, "u1")
        store.add_shoo(CHAT, event.id, 1, "кыш", "улетел", True, clock.now())
        store.close_event(event.id)
        earned = achievements.after_scare(store, clock, CHAT, 1)
        assert earned == ([achievements.SCARER] if number == 5 else [])
    assert achievements.after_scare(store, clock, CHAT, 1) == []


def test_attempts_that_failed_do_not_count_for_scarer(store, clock) -> None:
    for _ in range(5):
        event = game.arrive(store, clock, CHAT)
        store.add_shoo(CHAT, event.id, 1, "кыш", "сидит", False, clock.now())
        store.close_event(event.id)
    assert store.scares(CHAT, 1) == 0
    assert achievements.after_scare(store, clock, CHAT, 1) == []


def test_trophy_holder_needs_a_week_in_total(store, clock) -> None:
    store.set_trophy(CHAT, 1, "u1", START)
    store.set_trophy(CHAT, 2, "u2", START + timedelta(days=4))
    store.set_trophy(CHAT, 1, "u1", START + timedelta(days=5))
    achievements.check_trophy_holders(store, CHAT, START + timedelta(days=7))
    assert store.achievements_of(CHAT, 1) == []
    achievements.check_trophy_holders(store, CHAT, START + timedelta(days=8))
    assert store.achievements_of(CHAT, 1) == [achievements.TROPHY_HOLDER]
    assert store.achievements_of(CHAT, 2) == []
