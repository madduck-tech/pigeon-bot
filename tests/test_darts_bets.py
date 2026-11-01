from datetime import UTC, datetime

import pytest

from pigeon.clock import FixedClock
from pigeon.repo.store import Store
from pigeon.services import bets, darts, game

CHAT = -4001
TZ = "Europe/Moscow"  # UTC+3
NOON = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)  # 12:00 Moscow


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "pigeon.db")
    s.enable_chat(CHAT, NOON, TZ)
    yield s
    s.close()


@pytest.fixture
def clock():
    return FixedClock(NOON)


def _seed_points(store: Store, user_id: int, points: int, clock: FixedClock) -> None:
    store.add_points(CHAT, user_id, points, "seed", None, clock.now())


# darts


@pytest.mark.parametrize("value,expected", [(6, 3), (5, 2), (4, 1), (3, 0), (1, 0)])
def test_dart_points(value: int, expected: int) -> None:
    assert darts.dart_points(value) == expected


def test_one_throw_per_user_per_local_day(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    assert darts.check(store, clock, CHAT, event.id, 1) is darts.DartOutcome.OK
    assert darts.record(store, clock, CHAT, event.id, 1, "Anna", 6) == 3
    assert darts.check(store, clock, CHAT, event.id, 1) is darts.DartOutcome.ALREADY_TODAY
    assert darts.record(store, clock, CHAT, event.id, 1, "Anna", 6) is None
    assert store.leaderboard(CHAT) == [("Anna", 3)]


def test_throw_limit_resets_on_next_local_day(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    darts.record(store, clock, CHAT, event.id, 1, "Anna", 5)
    # Next local day, 00:30 Moscow = 21:30 UTC the day before.
    clock.set(datetime(2026, 11, 2, 21, 30, tzinfo=UTC))
    tomorrow = game.arrive(store, clock, CHAT)  # the earlier arrival has expired by now
    assert darts.check(store, clock, CHAT, tomorrow.id, 1) is darts.DartOutcome.OK


def test_dart_on_closed_arrival_is_rejected(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    clock.set(event.closes_at)
    assert darts.check(store, clock, CHAT, event.id, 1) is darts.DartOutcome.CLOSED


# bets


def test_bet_needs_a_point_to_stake(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    assert bets.place(store, clock, CHAT, event.id, 1, "Anna", "ge3") is bets.BetOutcome.NO_BALANCE


def test_bet_takes_stake_once(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    _seed_points(store, 1, 2, clock)
    assert bets.place(store, clock, CHAT, event.id, 1, "Anna", "ge3") is bets.BetOutcome.PLACED
    assert store.balance(CHAT, 1) == 1
    assert bets.place(store, clock, CHAT, event.id, 1, "Anna", "lt3") is bets.BetOutcome.ALREADY
    assert store.balance(CHAT, 1) == 1


def test_bet_rejects_unknown_choice(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    _seed_points(store, 1, 2, clock)
    with pytest.raises(ValueError):
        bets.place(store, clock, CHAT, event.id, 1, "Anna", "maybe")


def test_winning_bet_pays_out_stake_plus_one(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    _seed_points(store, 1, 1, clock)
    bets.place(store, clock, CHAT, event.id, 1, "Anna", "ge3")
    for user in (2, 3, 4):
        game.hit(store, clock, CHAT, event.id, user, f"u{user}")
    clock.set(event.closes_at)
    game.live_event(store, clock, CHAT, event.id)  # closes and settles
    assert store.balance(CHAT, 1) == 1 - bets.STAKE + bets.PAYOUT
    assert store.get_event(event.id).status == "closed"


def test_losing_bet_keeps_the_stake_gone(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    _seed_points(store, 1, 1, clock)
    bets.place(store, clock, CHAT, event.id, 1, "Anna", "ge3")
    game.hit(store, clock, CHAT, event.id, 2, "Boris")
    clock.set(event.closes_at)
    game.arrive(store, clock, CHAT)  # the next arrival closes the expired one
    assert store.balance(CHAT, 1) == 0


def test_bet_on_closed_arrival_is_rejected(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    _seed_points(store, 1, 2, clock)
    clock.set(event.closes_at)
    assert bets.place(store, clock, CHAT, event.id, 1, "Anna", "lt3") is bets.BetOutcome.CLOSED
    assert store.balance(CHAT, 1) == 2


def test_settlement_pays_only_once(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    _seed_points(store, 1, 1, clock)
    bets.place(store, clock, CHAT, event.id, 1, "Anna", "lt3")
    clock.set(event.closes_at)
    game.live_event(store, clock, CHAT, event.id)
    game.live_event(store, clock, CHAT, event.id)
    assert store.balance(CHAT, 1) == 1 - bets.STAKE + bets.PAYOUT
