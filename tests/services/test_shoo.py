import random
from datetime import UTC, datetime, timedelta

import pytest

from pigeon.clock import FixedClock
from pigeon.content import ShooTexts
from pigeon.repo.store import Store
from pigeon.services import game
from pigeon.services.shoo import ShooOutcome, flee_chance, shoo

CHAT = -8001
OTHER_CHAT = -8002
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
LINES = ShooTexts(actions=("кыш!",), stayed=("сидит",), flew=("улетел",))


class Rolls(random.Random):
    """random() returns the given rolls in order; choice() stays deterministic."""

    def __init__(self, *rolls: float) -> None:
        super().__init__(1)
        self.rolls = list(rolls)

    def random(self) -> float:
        return self.rolls.pop(0)

    def getrandbits(self, k: int) -> int:
        # Keeps choice() away from random(), so only the flight decision uses up a roll.
        return super().getrandbits(k)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "pigeon.db")
    s.enable_chat(CHAT, NOW, "UTC")
    s.enable_chat(OTHER_CHAT, NOW, "UTC")
    yield s
    s.close()


@pytest.fixture
def clock():
    return FixedClock(NOW)


def _shoo(store, clock, event_id, user_id, rng, chat_id=CHAT):
    return shoo(store, clock, rng, LINES, chat_id, event_id, user_id, f"u{user_id}")


@pytest.mark.parametrize(
    "previous,chance", [(0, 0.20), (1, 0.35), (2, 0.50), (3, 0.65), (5, 0.95), (6, 1.0), (40, 1.0)]
)
def test_chance_grows_with_each_attempt(previous, chance) -> None:
    assert flee_chance(previous) == pytest.approx(chance)


def test_roll_just_above_the_chance_keeps_the_pigeon(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    assert _shoo(store, clock, event.id, 1, Rolls(0.21)) is ShooOutcome.STAYED
    assert store.current_open_event(CHAT) is not None


def test_same_roll_works_for_the_next_person(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    _shoo(store, clock, event.id, 1, Rolls(0.21))
    assert _shoo(store, clock, event.id, 2, Rolls(0.21)) is ShooOutcome.FLEW
    assert store.current_open_event(CHAT) is None


def test_second_attempt_by_the_same_person_is_refused(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    _shoo(store, clock, event.id, 1, Rolls(0.99))
    # The roll would succeed, but the attempt must not count, or one person could finish alone.
    assert _shoo(store, clock, event.id, 1, Rolls(0.0)) is ShooOutcome.ALREADY
    assert store.shoo_count(CHAT, event.id) == 1
    assert store.current_open_event(CHAT) is not None


def test_scaring_gives_no_points_and_keeps_hits(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, event.id, 2, "u2")
    _shoo(store, clock, event.id, 1, Rolls(0.0))
    assert store.balance(CHAT, 1) == 0
    assert store.hit_count(event.id) == 1
    assert store.scares(CHAT, 1) == 1


def test_hit_after_the_pigeon_flew_is_refused(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    _shoo(store, clock, event.id, 1, Rolls(0.0))
    assert game.hit(store, clock, CHAT, event.id, 2, "u2") is game.HitOutcome.CLOSED


def test_shoo_after_the_window_closes_the_arrival(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    clock.set(event.closes_at + timedelta(seconds=1))
    assert _shoo(store, clock, event.id, 1, Rolls(0.0)) is ShooOutcome.CLOSED
    assert store.get_event(event.id).status == "closed"
    assert store.shoo_count(CHAT, event.id) == 0


def test_shoo_on_another_chats_pigeon_is_rejected(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    outcome = _shoo(store, clock, event.id, 1, Rolls(0.0), chat_id=OTHER_CHAT)
    assert outcome is ShooOutcome.NO_EVENT
    assert store.current_open_event(CHAT) is not None


def test_failed_send_is_kept_if_someone_shooed_it(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    _shoo(store, clock, event.id, 1, Rolls(0.99))
    assert store.delete_unsent_event(CHAT, event.id) is False


def test_a_new_pigeon_starts_from_the_base_chance(store, clock) -> None:
    first = game.arrive(store, clock, CHAT)
    _shoo(store, clock, first.id, 1, Rolls(0.99))
    clock.set(first.closes_at)
    second = game.arrive(store, clock, CHAT)
    assert store.shoo_count(CHAT, second.id) == 0
    assert _shoo(store, clock, second.id, 1, Rolls(0.21)) is ShooOutcome.STAYED
