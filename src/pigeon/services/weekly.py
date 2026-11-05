"""Local weekly quests and their persistent, per-chat deck."""

from __future__ import annotations

import random
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pigeon.content import Quest
from pigeon.render import texts
from pigeon.repo.store import QuestDeck, QuestRecord, Store


def bounds(now: datetime, timezone: str) -> tuple[str, datetime, datetime]:
    tz = ZoneInfo(timezone)
    day = now.astimezone(tz).date()
    monday = day - timedelta(days=day.weekday())
    start = datetime.combine(monday, time.min, tzinfo=tz)
    end = datetime.combine(monday + timedelta(days=7), time.min, tzinfo=tz)
    return monday.isoformat(), start.astimezone(UTC), end.astimezone(UTC)


def ensure(
    store: Store, chat_id: int, now: datetime, quests: tuple[Quest, ...], rng: random.Random
) -> QuestRecord | None:
    chat = store.get_chat(chat_id)
    if not chat or not quests:
        return None
    key, _, _ = bounds(now, chat.timezone)
    with store.transaction():
        row = store.get_quest(chat_id, key)
        if row is not None:
            return row
        store.fail_previous_quests(chat_id, key)
        deck = store.get_quest_deck(chat_id)
        remaining = deck.remaining[:] if deck else []
        last = deck.last_quest_id if deck else None
        ids = {q.id for q in quests}
        remaining = [x for x in remaining if x in ids]
        if not remaining:
            remaining = sorted(ids)
            rng.shuffle(remaining)
            if len(remaining) > 1 and remaining[0] == last:
                remaining[0], remaining[1] = remaining[1], remaining[0]
        chosen = remaining.pop(0)
        store.save_quest_deck(chat_id, QuestDeck(remaining, chosen))
        return store.create_quest(chat_id, key, chosen)


def current(
    store: Store, chat_id: int, now: datetime, quests: tuple[Quest, ...]
) -> tuple[QuestRecord, Quest, int] | None:
    chat = store.get_chat(chat_id)
    if not chat:
        return None
    key, start, end = bounds(now, chat.timezone)
    row = store.get_quest(chat_id, key)
    if row is None:
        return None
    quest = next((q for q in quests if q.id == row.quest_id), None)
    if quest is None:
        return None
    return row, quest, progress(store, chat_id, quest.id, start, end)


def progress(store: Store, chat_id: int, ident: str, start: datetime, end: datetime) -> int:
    if ident == "weekly-eight":
        return store.successful_hits_in_period(chat_id, start, end)
    if ident == "trio-hit":
        return store.max_hit_participants(chat_id, start, end)
    if ident == "team-scare":
        return store.max_shoo_participants(chat_id, start, end)
    return store.max_hits_before_flee(chat_id, start, end)


def check(
    store: Store, chat_id: int, event_id: int, now: datetime, quests: tuple[Quest, ...]
) -> None:
    with store.transaction():
        item = current(store, chat_id, now, quests)
        if item is None:
            return
        row, quest, value = item
        if row.status != "active" or value < quest.target:
            return
        store.complete_quest(chat_id, row.week_key, event_id, quest.title, now)


def label(item: tuple[QuestRecord, Quest, int] | None) -> str:
    if item is None:
        return ""
    row, quest, value = item
    mark = " ✅" if row.status == "completed" else ""
    return (
        f"🎯 <b>Задание недели: {texts._esc(quest.name)}</b>\n"
        f"{texts._esc(quest.description)} "
        f"({min(value, quest.target)}/{quest.target}){mark}"
    )


def last_title(store: Store, chat_id: int) -> str | None:
    return store.latest_chat_title(chat_id)


def monday_text(store: Store, chat_id: int, now: datetime, quests: tuple[Quest, ...]) -> str:
    chat = store.get_chat(chat_id)
    key, start, _ = bounds(now, chat.timezone)
    previous = (datetime.fromisoformat(key).date() - timedelta(days=7)).isoformat()
    prev = store.get_quest(chat_id, previous)
    lines = ["📅 <b>Новая неделя</b>"]
    if prev:
        quest = next((q for q in quests if q.id == prev.quest_id), None)
        if quest:
            result = (
                f"выполнено! Титул: {texts._esc(quest.title)}"
                if prev.status == "completed"
                else "не выполнено"
            )
            lines.append(f"Прошлое задание «{texts._esc(quest.name)}»: {result}.")
    _, prev_start, _ = bounds(start - timedelta(days=1), chat.timezone)
    rows = store.week_leaderboard(chat_id, prev_start, start, 3)
    if rows:
        lines.append("🏆 <b>Топ прошлой недели</b>")
        lines.extend(
            f"{i}. {texts._esc(name)} — {points}" for i, (name, points) in enumerate(rows, 1)
        )
    lines.append(label(current(store, chat_id, now, quests)))
    return "\n".join(lines)
