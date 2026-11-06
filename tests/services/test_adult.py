import json
import random
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from pigeon.clock import FixedClock
from pigeon.repo.store import PigeonDeck, Store
from pigeon.services import adult, game, pigeon_types
from rigged import CONTENT

NOW = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
CHAT = -3001


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "adult.db")
    value.enable_chat(CHAT, NOW, "UTC")
    value.set_adult_enabled(CHAT, True)
    yield value
    value.close()


@pytest.fixture
def clock():
    return FixedClock(NOW)


def _arrival(store, clock, target="cap"):
    event_id = store.open_event(
        CHAT,
        clock.now(),
        clock.now() + timedelta(minutes=15),
        pigeon_name="Глаша",
        pigeon_type="adult",
        type_state=json.dumps({"target": target}),
    )
    return store.get_event(event_id)


def _win(store, clock, event, user_id=2):
    assert (
        game.hit(
            store,
            clock,
            CHAT,
            event.id,
            user_id,
            "Boris",
            random.Random(1),
            CONTENT.hits,
            area="cap",
        )
        is game.HitOutcome.HIT
    )


@pytest.mark.parametrize("seed", range(40))
@pytest.mark.parametrize("last", [None, "normal", "capped", "sleepy", "golden", "adult"])
def test_adult_deck_contents_and_boundaries(seed, last):
    rng = random.Random(seed)
    deck = PigeonDeck([], last)
    types = []
    for _ in range(24):
        item, deck = pigeon_types.draw(rng, deck, adult_enabled=True)
        types.append(item)
    for offset in (0, 12):
        assert Counter(types[offset : offset + 12]) == {
            "normal": 8,
            "capped": 1,
            "sleepy": 1,
            "golden": 1,
            "adult": 1,
        }
    sequence = ([last] if last else []) + types
    assert all(a == "normal" or b == "normal" for a, b in zip(sequence, sequence[1:], strict=False))


def test_disabling_consumes_an_already_drawn_adult_as_normal():
    item, deck = pigeon_types.draw(random.Random(1), PigeonDeck(["adult", "normal"], "normal"))
    assert item == "normal"
    assert deck == PigeonDeck(["normal"], "normal")


def test_adult_arrival_preserves_target_and_window_over_restart(store, clock):
    store.save_pigeon_deck(CHAT, PigeonDeck(["adult"], "normal"))
    event = game.arrive(store, clock, CHAT, rng=random.Random(4))
    assert event.pigeon_type == "adult"
    assert event.closes_at - event.opened_at == timedelta(minutes=15)
    selected = adult.target(event)
    path = store._conn.execute("PRAGMA database_list").fetchone()[2]
    store.close()
    store = Store(path)
    restored = store.get_event(event.id)
    assert adult.target(restored) == selected
    store.close()


def test_area_hit_rules_and_only_first_winner_get_gift(store, clock):
    event = _arrival(store, clock)
    assert (
        game.hit(
            store, clock, CHAT, event.id, 1, "Anna", random.Random(1), CONTENT.hits, area="wing"
        )
        is game.HitOutcome.HIT
    )
    assert store.balance(CHAT, 1) == 0
    assert adult.gift_owner(store.get_event(event.id)) is None
    _win(store, clock, event, 2)
    _win(store, clock, event, 3)
    assert store.balance(CHAT, 2) == 1
    assert store.balance(CHAT, 3) == 1
    assert store.hit_count(event.id) == 2
    assert store.get_trophy(CHAT).holder_user_id == 2
    assert adult.gift_owner(store.get_event(event.id)) == 2
    assert (
        game.hit(
            store, clock, CHAT, event.id, 1, "Anna", random.Random(1), CONTENT.hits, area="cap"
        )
        is game.HitOutcome.ALREADY
    )


def test_forged_or_mismatched_area_does_not_consume_attempt(store, clock):
    event = _arrival(store, clock)
    for area in (None, "neck"):
        assert (
            game.hit(store, clock, CHAT, event.id, 1, "Anna", area=area) is game.HitOutcome.BAD_AREA
        )
    assert store._conn.execute("SELECT COUNT(*) FROM hits").fetchone()[0] == 0
    store.close_event(event.id)
    clock.set(event.closes_at + timedelta(seconds=1))
    normal = game.arrive(store, clock, CHAT)
    assert (
        game.hit(store, clock, CHAT, normal.id, 1, "Anna", area="cap") is game.HitOutcome.BAD_AREA
    )
    assert store._conn.execute("SELECT COUNT(*) FROM hits").fetchone()[0] == 0


def test_gift_failure_retry_and_sent_are_persistent(store, clock):
    event = _arrival(store, clock)
    _win(store, clock, event)
    assert (
        adult.begin_gift(store, clock, CHAT, event.id, 1, "other").outcome
        is adult.GiftOutcome.NOT_WINNER
    )
    started = adult.begin_gift(store, clock, CHAT, event.id, 2, "first")
    assert started.outcome is adult.GiftOutcome.START
    assert (
        adult.begin_gift(store, clock, CHAT, event.id, 2, "second").outcome
        is adult.GiftOutcome.IN_PROGRESS
    )
    assert adult.finish_gift(store, CHAT, event.id, "stale", 901) is False
    assert adult.finish_gift(store, CHAT, event.id, "first", None) is True
    assert store.balance(CHAT, 2) == 1
    retry = adult.begin_gift(store, clock, CHAT, event.id, 2, "second")
    assert retry.outcome is adult.GiftOutcome.START
    assert adult.finish_gift(store, CHAT, event.id, "second", 901) is True
    assert (
        adult.begin_gift(store, clock, CHAT, event.id, 2, "third").outcome is adult.GiftOutcome.SENT
    )
    assert adult.gift_visible(store.get_event(event.id), True) is False


def test_gift_lease_expires_and_closed_round_does_not_cancel(store, clock):
    event = _arrival(store, clock)
    _win(store, clock, event)
    store.close_event(event.id)
    assert (
        adult.begin_gift(store, clock, CHAT, event.id, 2, "first").outcome
        is adult.GiftOutcome.START
    )
    # Even an attempt cut off at its deadline still holds the lease.
    clock.set(NOW + adult.GIFT_ATTEMPT_DEADLINE)
    assert (
        adult.begin_gift(store, clock, CHAT, event.id, 2, "second").outcome
        is adult.GiftOutcome.IN_PROGRESS
    )
    clock.set(NOW + timedelta(seconds=119))
    assert (
        adult.begin_gift(store, clock, CHAT, event.id, 2, "second").outcome
        is adult.GiftOutcome.IN_PROGRESS
    )
    clock.set(NOW + timedelta(seconds=120))
    assert (
        adult.begin_gift(store, clock, CHAT, event.id, 2, "second").outcome
        is adult.GiftOutcome.START
    )


def test_gift_lease_outlives_the_attempt_deadline():
    # Otherwise a second tap could start a parallel attempt while the first can still post.
    assert timedelta(minutes=2) == adult.GIFT_LEASE
    assert timedelta(seconds=90) == adult.GIFT_ATTEMPT_DEADLINE
    assert adult.GIFT_LEASE > adult.GIFT_ATTEMPT_DEADLINE


def test_gift_is_scoped_and_disabled_without_losing_hit(store, clock):
    event = _arrival(store, clock)
    _win(store, clock, event)
    assert (
        adult.begin_gift(store, clock, CHAT - 1, event.id, 2, "other").outcome
        is adult.GiftOutcome.NO_EVENT
    )
    store.set_adult_enabled(CHAT, False)
    assert (
        adult.begin_gift(store, clock, CHAT, event.id, 2, "off").outcome
        is adult.GiftOutcome.DISABLED
    )
    assert store.balance(CHAT, 2) == 1
