import random
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from pigeon.clock import FixedClock
from pigeon.repo.store import Store
from pigeon.services import game
from rigged import CONTENT

NOW = datetime(2026, 1, 1, tzinfo=UTC)
CHAT = -55


class Roll(random.Random):
    def __init__(self, value):
        super().__init__(1)
        self.value = value

    def random(self):
        return self.value


@pytest.mark.parametrize(
    ("roll", "outcome", "points"),
    [
        (0, "normal", 1),
        (0.64999, "normal", 1),
        (0.65, "miss", 0),
        (0.84999, "miss", 0),
        (0.85, "cap", 2),
        (0.94999, "cap", 2),
        (0.95, "chain", 3),
        (0.99999, "chain", 3),
    ],
)
def test_outcome_boundaries_and_attempts(roll, outcome, points):
    store = Store(":memory:")
    clock = FixedClock(NOW)
    store.enable_chat(CHAT, NOW, "UTC")
    event = game.arrive(store, clock, CHAT)
    assert (
        game.hit(store, clock, CHAT, event.id, 1, "A", Roll(roll), CONTENT.hits)
        == game.HitOutcome.HIT
    )
    row = store._conn.execute("SELECT outcome, points, reaction FROM hits").fetchone()
    assert (row["outcome"], row["points"]) == (outcome, points)
    assert row["reaction"] in CONTENT.hits[outcome]
    assert store.balance(CHAT, 1) == points
    assert store.hit_count(event.id) == (points > 0)
    assert bool(store.get_trophy(CHAT)) == (points > 0)
    assert store._conn.execute("SELECT COUNT(*) FROM points_ledger").fetchone()[0] == (points > 0)
    assert (
        game.hit(store, clock, CHAT, event.id, 1, "A", Roll(0), CONTENT.hits)
        == game.HitOutcome.ALREADY
    )
    store.close()


def test_types_deck_survives_restart_and_unsent_restores_draw(tmp_path):
    path = tmp_path / "deck.db"
    store = Store(path)
    clock = FixedClock(NOW)
    store.enable_chat(CHAT, NOW, "UTC")
    rng = random.Random(23)
    drawn = []
    for i in range(36):
        event = game.arrive(store, clock, CHAT, rng=rng)
        if i == 7:
            assert game.delete_unsent_event(store, CHAT, event.id)
            event2 = game.arrive(store, clock, CHAT, rng=rng)
            assert event.pigeon_type == event2.pigeon_type
            event = event2
            store.close()
            store = Store(path)
        drawn.append(event.pigeon_type)
        assert event.closes_at - event.opened_at == timedelta(
            minutes=5 if event.pigeon_type == "golden" else 15
        )
        clock.set(event.closes_at)
    for offset in (0, 12, 24):
        assert Counter(drawn[offset : offset + 12]) == {
            "normal": 9,
            "capped": 1,
            "sleepy": 1,
            "golden": 1,
        }
    assert all(a == "normal" or b == "normal" for a, b in zip(drawn, drawn[1:], strict=False))
    store.close()


def test_capped_then_success_and_golden_multiplier():
    store = Store(":memory:")
    clock = FixedClock(NOW)
    store.enable_chat(CHAT, NOW, "UTC")
    event = game.arrive(store, clock, CHAT)
    store._conn.execute("UPDATE events SET pigeon_type='capped' WHERE id=?", (event.id,))
    store._conn.commit()
    game.hit(store, clock, CHAT, event.id, 1, "A", Roll(0), CONTENT.hits)
    assert store.hit_count(event.id) == 0
    assert store.get_trophy(CHAT) is None
    game.hit(store, clock, CHAT, event.id, 2, "B", Roll(0.85), CONTENT.hits)
    assert store.hit_count(event.id) == 1
    assert store.get_trophy(CHAT).holder_user_id == 2
    assert store.balance(CHAT, 2) == 2
    store._conn.execute("UPDATE events SET pigeon_type='golden' WHERE id=?", (event.id,))
    store._conn.commit()
    game.hit(store, clock, CHAT, event.id, 3, "C", Roll(0.95), CONTENT.hits)
    assert store.balance(CHAT, 3) == 6
    store.close()


def test_history_combines_hits_and_shoos_with_same_timestamp():
    store = Store(":memory:")
    clock = FixedClock(NOW)
    store.enable_chat(CHAT, NOW, "UTC")
    event = game.arrive(store, clock, CHAT)
    for user in range(1, 5):
        store.upsert_member(CHAT, user, str(user))
        if user % 2:
            store.add_hit(CHAT, event.id, user, NOW, 1, "normal", "попал")
        else:
            store.add_shoo(CHAT, event.id, user, "топнул", "сидит", False, NOW)
    assert [item["name"] for item in store.actions_of(CHAT, event.id)] == ["2", "3", "4"]
    assert "попал" in game.arrival_text(store, event)
    store.close()


def test_sleepy_shoo_bonus():
    from pigeon.services.shoo import ShooOutcome, shoo

    store = Store(":memory:")
    clock = FixedClock(NOW)
    store.enable_chat(CHAT, NOW, "UTC")
    event = game.arrive(store, clock, CHAT)
    store._conn.execute("UPDATE events SET pigeon_type='sleepy' WHERE id=?", (event.id,))
    store._conn.commit()
    assert shoo(store, clock, Roll(0.34), CONTENT.shoos, CHAT, event.id, 1, "A") == ShooOutcome.FLEW
    store.close()
