from datetime import UTC, datetime, timedelta

import pytest

from pigeon.clock import FixedClock
from pigeon.repo.store import Store
from pigeon.services import game
from pigeon.services.game import HitOutcome, NotEnabled

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
CHAT = -1001
OTHER_CHAT = -1002


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "pigeon.db")
    yield s
    s.close()


@pytest.fixture
def clock():
    return FixedClock(T0)


def test_arrive_requires_setup(store, clock) -> None:
    with pytest.raises(NotEnabled):
        game.arrive(store, clock, CHAT)


def test_arrive_opens_event_and_refuses_second_while_sitting(store, clock) -> None:
    store.enable_chat(CHAT, T0, "UTC")
    event = game.arrive(store, clock, CHAT)
    assert event is not None
    assert event.status == "open"
    assert event.closes_at == T0 + timedelta(minutes=15)
    assert game.arrive(store, clock, CHAT) is None


def test_hit_counts_once_per_user_and_scores_one_point(store, clock) -> None:
    store.enable_chat(CHAT, T0, "UTC")
    event = game.arrive(store, clock, CHAT)
    assert game.hit(store, clock, CHAT, event.id, 1, "Anna") is HitOutcome.HIT
    assert game.hit(store, clock, CHAT, event.id, 1, "Anna") is HitOutcome.ALREADY
    assert game.hit(store, clock, CHAT, event.id, 2, "Boris") is HitOutcome.HIT
    assert store.hit_count(event.id) == 2
    assert store.leaderboard(CHAT) == [("Anna", 1), ("Boris", 1)]


def test_hit_after_window_closes_event(store, clock) -> None:
    store.enable_chat(CHAT, T0, "UTC")
    event = game.arrive(store, clock, CHAT)
    clock.set(event.closes_at + timedelta(seconds=1))
    assert game.hit(store, clock, CHAT, event.id, 1, "Anna") is HitOutcome.CLOSED
    assert store.get_event(event.id).status == "closed"
    assert store.leaderboard(CHAT) == []


def test_new_arrival_allowed_after_previous_window(store, clock) -> None:
    store.enable_chat(CHAT, T0, "UTC")
    first = game.arrive(store, clock, CHAT)
    clock.set(first.closes_at)
    second = game.arrive(store, clock, CHAT)
    assert second is not None and second.id != first.id
    assert store.get_event(first.id).status == "closed"


def test_hit_on_other_chats_event_is_rejected(store, clock) -> None:
    store.enable_chat(CHAT, T0, "UTC")
    store.enable_chat(OTHER_CHAT, T0, "UTC")
    event = game.arrive(store, clock, CHAT)
    assert game.hit(store, clock, OTHER_CHAT, event.id, 1, "Anna") is HitOutcome.NO_EVENT
    assert store.leaderboard(OTHER_CHAT) == []


def test_leaderboard_is_scoped_per_chat(store, clock) -> None:
    store.enable_chat(CHAT, T0, "UTC")
    store.enable_chat(OTHER_CHAT, T0, "UTC")
    event = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, event.id, 1, "Anna")
    assert store.leaderboard(OTHER_CHAT) == []
    assert store.leaderboard(CHAT) == [("Anna", 1)]
