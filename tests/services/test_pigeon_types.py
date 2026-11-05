import random
from collections import Counter
from datetime import UTC, datetime

import pytest

from pigeon.clock import FixedClock
from pigeon.repo.store import PigeonDeck, Store
from pigeon.services import game, pigeon_types


@pytest.mark.parametrize("seed", range(40))
@pytest.mark.parametrize("last", [None, "normal", "capped", "sleepy", "golden"])
def test_deck_contents_and_boundaries(seed, last):
    rng = random.Random(seed)
    deck = PigeonDeck([], last)
    types = []
    for _ in range(24):
        item, deck = pigeon_types.draw(rng, deck)
        types.append(item)
    for offset in (0, 12):
        assert Counter(types[offset : offset + 12]) == {
            "normal": 9,
            "capped": 1,
            "sleepy": 1,
            "golden": 1,
        }
    sequence = ([last] if last else []) + types
    assert all(a == "normal" or b == "normal" for a, b in zip(sequence, sequence[1:], strict=False))


def test_failed_open_rolls_back_deck_and_event(monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = Store(":memory:")
    store.enable_chat(-1, now, "UTC")

    def fail(*args, **kwargs):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(store, "open_event", fail)
    with pytest.raises(RuntimeError, match="insert failed"):
        game.arrive(store, FixedClock(now), -1, rng=random.Random(1))
    assert store.get_pigeon_deck(-1) is None
    assert store.current_open_event(-1) is None
    store.close()


def test_failed_restore_rolls_back_event_deletion(monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = Store(":memory:")
    store.enable_chat(-1, now, "UTC")
    event = game.arrive(store, FixedClock(now), -1, rng=random.Random(1))
    deck = store.get_pigeon_deck(-1)

    def fail(*args, **kwargs):
        raise RuntimeError("save failed")

    monkeypatch.setattr(store, "save_pigeon_deck", fail)
    with pytest.raises(RuntimeError, match="save failed"):
        game.delete_unsent_event(store, -1, event.id)
    assert store.get_event(event.id) is not None
    assert store.get_pigeon_deck(-1) == deck
    store.close()
