"""Per-chat pigeon type decks and draws."""

import random

from pigeon.repo.store import PigeonDeck


def draw(rng: random.Random, deck: PigeonDeck | None) -> tuple[str, PigeonDeck]:
    remaining = list(deck.remaining) if deck else []
    last = deck.last_type if deck else None
    if not remaining:
        remaining = ["normal"] * 9 + ["capped", "sleepy", "golden"]
        rng.shuffle(remaining)
        specials = [item for item in remaining if item != "normal"]
        slots = list(range(1 if last not in (None, "normal") else 0, 12))
        rng.shuffle(slots)
        chosen = []
        for slot in slots:
            if all(abs(slot - other) > 1 for other in chosen):
                chosen.append(slot)
            if len(chosen) == 3:
                break
        remaining = ["normal"] * 12
        for slot, special in zip(chosen, specials, strict=True):
            remaining[slot] = special
    pigeon_type = remaining.pop(0)
    return pigeon_type, PigeonDeck(remaining, pigeon_type)


def restore(deck: PigeonDeck, pigeon_type: str, previous_type: str | None) -> PigeonDeck:
    return PigeonDeck([pigeon_type, *deck.remaining], previous_type)
