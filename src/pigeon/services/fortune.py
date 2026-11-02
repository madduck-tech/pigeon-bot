"""Fortunes: one personal prediction per user per local day, and one daily post per chat."""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import datetime

from pigeon.budget import limits
from pigeon.clock import Clock
from pigeon.repo.store import ChatSettings, Store

HOURS_AFTER_QUIET = 1


def daily_due(chat: ChatSettings, now: datetime) -> bool:
    """Whether the bot may post today's fortune now: it has to be free to post, and morning.

    The hour is capped by quiet_start, so a short day still has a moment for the post.
    """
    hour = min(chat.quiet_end + HOURS_AFTER_QUIET, chat.quiet_start - 1)
    return limits.can_publish(chat, now) and limits.chat_local(chat, now).hour >= hour


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
