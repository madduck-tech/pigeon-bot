"""Per-chat pigeon type decks and draws."""

import random

from pigeon.repo.store import PigeonDeck


def draw(
    rng: random.Random, deck: PigeonDeck | None, adult_enabled: bool = False
) -> tuple[str, PigeonDeck]:
    remaining = list(deck.remaining) if deck else []
    last = deck.last_type if deck else None
    if not remaining:
        specials = ["capped", "sleepy", "golden"]
        if adult_enabled:
            specials.append("adult")
        rng.shuffle(specials)
        slots = list(range(1 if last not in (None, "normal") else 0, 12))
        rng.shuffle(slots)
        chosen = []
        for slot in slots:
            if all(abs(slot - other) > 1 for other in chosen):
                chosen.append(slot)
            if len(chosen) == len(specials):
                break
        remaining = ["normal"] * 12
        for slot, special in zip(chosen, specials, strict=True):
            remaining[slot] = special
    pigeon_type = remaining.pop(0)
    if pigeon_type == "adult" and not adult_enabled:
        pigeon_type = "normal"
    return pigeon_type, PigeonDeck(remaining, pigeon_type)


def restore(deck: PigeonDeck, pigeon_type: str, previous_type: str | None) -> PigeonDeck:
    return PigeonDeck([pigeon_type, *deck.remaining], previous_type)
