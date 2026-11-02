"""/pigeon fortune: one random prediction per user per local day."""

from __future__ import annotations

import random
from collections.abc import Sequence

from pigeon.budget import limits
from pigeon.clock import Clock
from pigeon.repo.store import ChatSettings, Store


def draw(
    store: Store,
    clock: Clock,
    rng: random.Random,
    fortunes: Sequence[str],
    chat: ChatSettings,
    user_id: int,
) -> str | None:
    """A prediction, or None if the user already had one today."""
    local_day = limits.chat_local(chat, clock.now()).date().isoformat()
    if not store.claim_fortune(chat.chat_id, user_id, local_day):
        return None
    return rng.choice(fortunes)
