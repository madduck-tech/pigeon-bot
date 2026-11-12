"""Prepare persistent personal draws and reserved courier predictions."""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import date, timedelta

from pigeon.content import Fortune, FortuneFormat
from pigeon.render.texts import courier_fortune, render_fortune
from pigeon.repo.store import FortuneDeck, Store

FORTUNE_CHAT_EXCLUSION_DAYS = 14


def draw_card(store, chat_id, owner_id, local_day, fortunes, names, rng) -> tuple[str, str]:
    """Draw in the caller's transaction; persistence of its consumer must be atomic."""
    daily = owner_id == 0
    scope = "daily" if daily else "personal"
    by_id = {f.id: f for f in fortunes}
    ids = set(by_id)
    deck = store.get_fortune_deck(chat_id, scope, owner_id)
    remaining = [x for x in deck.remaining if x in ids] if deck else []
    known = set(deck.known) if deck else set()
    last = deck.last_fortune_id if deck else None
    cycle = deck.cycle if deck else 0
    for ident in sorted(ids - known):
        remaining.insert(rng.randrange(len(remaining) + 1), ident)
    excluded: set[str] = set()
    if not daily:
        start = date.fromisoformat(local_day) - timedelta(days=FORTUNE_CHAT_EXCLUSION_DAYS - 1)
        excluded = store.fortune_history(chat_id, start.isoformat(), local_day)
    if not any(x not in excluded for x in remaining):
        remaining = sorted(ids)
        rng.shuffle(remaining)
        cycle += 1
        if len(remaining) > 1 and remaining[0] == last:
            remaining[0], remaining[1] = remaining[1], remaining[0]
    eligible = [x for x in remaining if x not in excluded]
    if not eligible:
        raise ValueError("no fortune available outside 14-day history")
    chosen = eligible[0]
    remaining.remove(chosen)
    item = by_id[chosen]
    rendered = render_fortune(item, rng.choice(names) if item.format == FortuneFormat.SCENE else "")
    store.save_fortune_deck(
        chat_id, scope, owner_id, FortuneDeck(cycle, remaining, [], chosen, sorted(ids))
    )
    return chosen, rendered


def prepare(
    store: Store,
    chat_id: int,
    owner_id: int,
    local_day: str,
    fortunes: Sequence[Fortune],
    names: Sequence[str],
    rng: random.Random,
) -> str | None:
    """Personal issue; owner zero is retained only for compatibility with historical callers."""
    with store.fortune_transaction():
        issue = store.get_fortune_issue(chat_id, owner_id, local_day)
        if issue is not None:
            return issue.rendered_text if owner_id == 0 and issue.sent_at is None else None
        chosen, rendered = draw_card(store, chat_id, owner_id, local_day, fortunes, names, rng)
        store.save_fortune_issue(chat_id, owner_id, local_day, chosen, rendered)
        return rendered


def migrate_courier(store: Store, fortunes: Sequence[Fortune]) -> None:
    """Move legacy pending standalone draws, cancel removed parts, preserve Monday posts."""
    if store.courier_migrated():
        return
    valid = {f.id for f in fortunes}
    with store.transaction():
        pending = [
            (row["chat_id"], row["fortune_id"], courier_fortune(row["rendered_text"]))
            for row in store.legacy_pending_fortunes()
            if row["fortune_id"] in valid and row["rendered_text"]
        ]
        store.upgrade_courier(pending)


def prepare_courier(store, chat_id, local_day, fortunes, names, rng):
    with store.fortune_transaction():
        pending = store.pending_courier(chat_id)
        if pending is None:
            ident, rendered = draw_card(store, chat_id, 0, local_day, fortunes, names, rng)
            store.enqueue_courier(chat_id, ident, courier_fortune(rendered))
            pending = store.pending_courier(chat_id)
        return pending
