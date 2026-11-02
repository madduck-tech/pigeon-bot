"""The weekly shared quest. Progress is counted from hits, so it cannot drift from them.

A week's quest row is created on first need, not by a job, so a hit right after Monday 00:00
always counts for the new week even if the scheduler has not ticked yet.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pigeon.budget import limits
from pigeon.clock import Clock
from pigeon.render import texts
from pigeon.repo.store import ChatSettings, Quest, Store

TARGET = 10


def week_key(local_now: datetime) -> str:
    return (local_now - timedelta(days=local_now.weekday())).date().isoformat()


def last_finished_week(chat: ChatSettings, now: datetime) -> str:
    return week_key(limits.chat_local(chat, now) - timedelta(days=7))


def _current(store: Store, chat: ChatSettings, now: datetime) -> tuple[Quest, int]:
    local = limits.chat_local(chat, now)
    quest = store.ensure_quest(chat.chat_id, week_key(local), TARGET)
    start = limits.local_week_start_utc(local)
    return quest, store.hits_between(chat.chat_id, start, limits.next_week_start_utc(local))


def progress(store: Store, chat: ChatSettings, now: datetime) -> tuple[int, int]:
    """(hits this week, target) for the dashboard."""
    quest, hits = _current(store, chat, now)
    return hits, quest.target


def after_hit(store: Store, clock: Clock, chat: ChatSettings) -> None:
    """Complete the quest once the week's hits reach the target.

    A failed quest can still complete: a timezone change can close a week early.
    """
    quest, hits = _current(store, chat, clock.now())
    if quest.status != "done" and hits >= quest.target:
        store.complete_quest(chat.chat_id, quest.id, texts.QUEST_TITLE)


def week_report(store: Store, chat: ChatSettings, key: str) -> str | None:
    """The result message for that week, or None if there is nothing to say or it was said."""
    quest = store.get_quest(chat.chat_id, key)
    if quest is None or quest.reported:
        return None
    start, end = limits.week_bounds_utc(chat, key)
    hits = store.hits_between(chat.chat_id, start, end)
    if hits == 0:
        return None
    return texts.quest_result(quest.status == "done", hits, quest.target)


def close_week(store: Store, chat: ChatSettings, key: str) -> None:
    store.close_quests_through(chat.chat_id, key)
