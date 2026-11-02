from datetime import UTC, datetime, timedelta

import pytest

from pigeon.clock import FixedClock
from pigeon.repo.store import Store
from pigeon.services import achievements, darts, game

CHAT = -5001
TZ = "Europe/Moscow"
START = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
NOVEMBER_1 = datetime(2026, 10, 31, 21, 0, tzinfo=UTC)  # 00:00 on Nov 1 in Moscow


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


@pytest.mark.parametrize("value,earned", [(6, ["bullseye"]), (5, [])])
def test_bullseye_only_for_a_six(store, clock, value, earned) -> None:
    assert achievements.after_dart(store, clock, CHAT, 1, value) == earned


def test_second_bullseye_is_not_announced(store, clock) -> None:
    achievements.after_dart(store, clock, CHAT, 1, 6)
    assert achievements.after_dart(store, clock, CHAT, 1, 6) == []


def test_friend_for_a_month_without_hits(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    darts.record(store, clock, CHAT, event.id, 3, "dart only", 2)  # known, never hit
    clock.set(clock.now() + timedelta(days=1))
    for _ in range(4):
        _arrival(store, clock, [1])
    chat = store.get_chat(CHAT)
    achievements.close_month(store, chat, NOVEMBER_1)
    assert achievements.FRIEND in store.achievements_of(CHAT, 3)
    assert store.achievements_of(CHAT, 1) == []


def test_no_friend_when_too_few_arrivals(store, clock) -> None:
    store.upsert_member(CHAT, 3, "lurker")
    for _ in range(4):
        _arrival(store, clock, [1])
    achievements.close_month(store, store.get_chat(CHAT), NOVEMBER_1)
    assert store.achievements_of(CHAT, 3) == []


def test_friend_month_uses_chat_local_boundaries(store, clock) -> None:
    # 22:00 UTC on Sep 30 is already October 1 in Moscow, so this hit is in October.
    store.upsert_member(CHAT, 3, "u3")
    clock.set(datetime(2026, 9, 30, 22, 0, tzinfo=UTC))
    _arrival(store, clock, [3])
    clock.set(START)
    for _ in range(4):
        _arrival(store, clock, [1])
    achievements.close_month(store, store.get_chat(CHAT), NOVEMBER_1)
    assert store.achievements_of(CHAT, 3) == []


def test_friend_is_not_decided_before_the_month_ends_locally(store, clock) -> None:
    # Regression: a job timed in Tokyo fired on Oct 31 in New York and granted October early.
    store.enable_chat(CHAT, START, "America/New_York")
    store.upsert_member(CHAT, 3, "u3")
    for _ in range(5):
        _arrival(store, clock, [1])
    early = datetime(2026, 10, 31, 15, 0, tzinfo=UTC)  # still October in New York
    achievements.close_month(store, store.get_chat(CHAT), early)
    assert store.achievements_of(CHAT, 3) == []
