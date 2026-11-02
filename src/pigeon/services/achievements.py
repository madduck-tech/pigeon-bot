"""Achievements: granted once per (chat, user, code). Conditions only; texts are in content/."""

from __future__ import annotations

from datetime import datetime, timedelta

from pigeon.budget import limits
from pigeon.clock import Clock
from pigeon.repo.store import ChatSettings, Store

SNIPER = "sniper"
EARLY_BIRD = "early_bird"
BULLSEYE = "bullseye"
FRIEND = "friend"
TROPHY_HOLDER = "trophy_holder"
DUELIST = "duelist"
CODES = (SNIPER, EARLY_BIRD, BULLSEYE, FRIEND, TROPHY_HOLDER, DUELIST)

SNIPER_STREAK = 5
EARLY_BIRD_FIRSTS = 5
BULLSEYE_VALUE = 6
FRIEND_MIN_ARRIVALS = 5
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


def after_dart(
    store: Store, clock: Clock, chat_id: int, user_id: int, dice_value: int
) -> list[str]:
    if dice_value == BULLSEYE_VALUE and store.award(chat_id, user_id, BULLSEYE, clock.now()):
        return [BULLSEYE]
    return []


def after_duel_win(store: Store, clock: Clock, chat_id: int, user_id: int) -> list[str]:
    return [DUELIST] if store.award(chat_id, user_id, DUELIST, clock.now()) else []


def check_trophy_holders(store: Store, chat_id: int, now: datetime) -> None:
    """Grant `trophy_holder` by total time held. Former holders count, so downtime loses nobody."""
    for user_id in store.trophy_user_ids(chat_id):
        if store.trophy_held(chat_id, user_id, now) >= TROPHY_HOLDER_TIME:
            store.award(chat_id, user_id, TROPHY_HOLDER, now)


def close_month(store: Store, chat: ChatSettings, now: datetime) -> None:
    """Grant `friend` for the last month finished at `now`. Safe to run twice."""
    last_of_previous = limits.chat_local(chat, now).replace(day=1) - timedelta(days=1)
    start, end = limits.month_bounds_utc(chat, last_of_previous.date().replace(day=1).isoformat())
    if store.arrivals_between(chat.chat_id, start, end) < FRIEND_MIN_ARRIVALS:
        return
    hitters = store.hitters_between(chat.chat_id, start, end)
    for user_id in store.member_ids(chat.chat_id) - hitters:
        store.award(chat.chat_id, user_id, FRIEND, now)
