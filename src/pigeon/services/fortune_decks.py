"""Prepare persistent personal and daily fortune draws."""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import date, timedelta

from pigeon.content import Fortune, FortuneFormat
from pigeon.render.texts import render_fortune
from pigeon.repo.store import FortuneDeck, Store

FORTUNE_CHAT_EXCLUSION_DAYS = 14


def prepare(
    store: Store,
    chat_id: int,
    owner_id: int,
    local_day: str,
    fortunes: Sequence[Fortune],
    names: Sequence[str],
    rng: random.Random,
) -> str | None:
    """Prepare one issue atomically, or return the existing pending daily text."""
    daily = owner_id == 0
    with store.fortune_transaction():
        issue = store.get_fortune_issue(chat_id, owner_id, local_day)
        if issue is not None:
            return issue.rendered_text if daily and issue.sent_at is None else None
        scope = "daily" if daily else "personal"
        by_id = {f.id: f for f in fortunes if daily or f.format != FortuneFormat.SERIAL}
        ids = {f.id for f in by_id.values() if f.format != FortuneFormat.SERIAL or f.part == 1}
        deck = store.get_fortune_deck(chat_id, scope, owner_id)
        remaining = [x for x in deck.remaining if x in ids] if deck else []
        forced = [x for x in deck.forced if x in by_id] if deck else []
        known = set(deck.known) if deck else set()
        last = deck.last_fortune_id if deck else None
        cycle = deck.cycle if deck else 0
        for ident in sorted(ids - known):
            remaining.insert(rng.randrange(len(remaining) + 1), ident)
        excluded: set[str] = set()
        if not daily:
            start = date.fromisoformat(local_day) - timedelta(days=FORTUNE_CHAT_EXCLUSION_DAYS - 1)
            excluded = store.fortune_history(chat_id, start.isoformat(), local_day)
        if not forced and not any(x not in excluded for x in remaining):
            remaining = sorted(ids)
            rng.shuffle(remaining)
            cycle += 1
            if len(remaining) > 1 and remaining[0] == last:
                remaining[0], remaining[1] = remaining[1], remaining[0]
        if forced:
            chosen = forced.pop(0)
            if chosen in remaining:
                remaining.remove(chosen)
        else:
            eligible = [x for x in remaining if x not in excluded]
            if not eligible:
                raise ValueError("no fortune available outside 14-day history")
            chosen = eligible[0]
            remaining.remove(chosen)
        item = by_id[chosen]
        if daily and item.format == FortuneFormat.SERIAL and item.part == 1:
            nxt = item.next
            while nxt:
                forced.append(nxt)
                if nxt in remaining:
                    remaining.remove(nxt)
                nxt = by_id[nxt].next
        rendered = render_fortune(
            item, rng.choice(names) if item.format == FortuneFormat.SCENE else ""
        )
        store.save_fortune_deck(
            chat_id, scope, owner_id, FortuneDeck(cycle, remaining, forced, chosen, sorted(ids))
        )
        store.save_fortune_issue(chat_id, owner_id, local_day, chosen, rendered)
        return rendered
