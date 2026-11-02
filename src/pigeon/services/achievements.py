"""Achievements: granted once per (chat, user, code). Conditions only; texts are in content/."""

from __future__ import annotations

from datetime import datetime, timedelta

from pigeon.clock import Clock
from pigeon.repo.store import Store

SNIPER = "sniper"
EARLY_BIRD = "early_bird"
TROPHY_HOLDER = "trophy_holder"
SCARER = "scarer"
CODES = (SNIPER, EARLY_BIRD, TROPHY_HOLDER, SCARER)

SNIPER_STREAK = 5
EARLY_BIRD_FIRSTS = 5
SCARER_PIGEONS = 5
TROPHY_HOLDER_TIME = timedelta(days=7)


def after_hit(store: Store, clock: Clock, chat_id: int, user_id: int) -> list[str]:
    """Check the hit-based achievements. Returns the codes granted just now."""
    now = clock.now()
    earned = []
    if store.hits_in_last_arrivals(chat_id, user_id, SNIPER_STREAK) >= SNIPER_STREAK:
        earned.append(SNIPER)
    if store.first_hits(chat_id, user_id) >= EARLY_BIRD_FIRSTS:
        earned.append(EARLY_BIRD)
    return [code for code in earned if store.award(chat_id, user_id, code, now)]


def after_scare(store: Store, clock: Clock, chat_id: int, user_id: int) -> list[str]:
    """Called when this user's attempt scared the pigeon off."""
    if store.scares(chat_id, user_id) >= SCARER_PIGEONS and store.award(
        chat_id, user_id, SCARER, clock.now()
    ):
        return [SCARER]
    return []


def check_trophy_holders(store: Store, chat_id: int, now: datetime) -> None:
    """Grant `trophy_holder` by total time held. Former holders count, so downtime loses nobody."""
    for user_id in store.trophy_user_ids(chat_id):
        if store.trophy_held(chat_id, user_id, now) >= TROPHY_HOLDER_TIME:
            store.award(chat_id, user_id, TROPHY_HOLDER, now)
