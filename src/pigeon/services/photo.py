"""Photo poll: is this a pigeon or a crow? At most once an hour per chat. No points.

Captions are only checked here, never stored.
"""

from __future__ import annotations

import re
from datetime import timedelta

from pigeon.budget import limits
from pigeon.clock import Clock
from pigeon.repo.store import ChatSettings, Store

COOLDOWN = timedelta(hours=1)
# Noun endings only: a bare "голуб" prefix would also match "голубой".
_PIGEON_WORD = re.compile(r"\bголуб(?:ь|я|ю|ем|е|и|ей|ям|ями|ях)\b", re.IGNORECASE)
_PIGEON_EMOJI = "🕊"


def mentions_pigeon(caption: str | None) -> bool:
    if not caption:
        return False
    return _PIGEON_EMOJI in caption or _PIGEON_WORD.search(caption) is not None


def claim(store: Store, clock: Clock, chat_id: int) -> bool:
    """Take the hourly slot. True if a poll may go out now."""
    now = clock.now()
    return store.claim_photo_poll(chat_id, now, now - COOLDOWN)


def claim_for_caption(store: Store, clock: Clock, chat: ChatSettings, caption: str | None) -> bool:
    """Nobody called the bot here, so unlike the command this obeys quiet hours and pause."""
    if not mentions_pigeon(caption) or not limits.can_publish(chat, clock.now()):
        return False
    return claim(store, clock, chat.chat_id)
