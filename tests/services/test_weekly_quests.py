import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pigeon.clock import FixedClock
from pigeon.content import load_content
from pigeon.repo.store import Store
from pigeon.scheduler import fortunes
from pigeon.services import weekly

NOW = datetime(2026, 11, 2, 7, tzinfo=UTC)
QUESTS = load_content(Path("content")).quests


def test_bounds_respect_local_monday():
    key, start, end = weekly.bounds(datetime(2026, 11, 1, 21, tzinfo=UTC), "Europe/Moscow")
    assert key == "2026-11-02"
    assert start == datetime(2026, 11, 1, 21, tzinfo=UTC)
    assert end == datetime(2026, 11, 8, 21, tzinfo=UTC)


def test_label_identifies_weekly_quest(tmp_path):
    store = Store(tmp_path / "db")
    store.enable_chat(1, NOW, "UTC")
    store.create_quest(1, "2026-11-02", QUESTS[0].id)
    assert weekly.label(weekly.current(store, 1, NOW, QUESTS)).startswith(
        "🎯 <b>Задание недели:"
    )
    store.close()


@pytest.mark.parametrize("ident", ["trio-hit", "team-scare", "weekly-eight", "hit-then-scare"])
def test_quest_completion_once_and_isolated(tmp_path, ident):
    path = tmp_path / "db"
    store = Store(path)
    for chat in (1, 2):
        store.enable_chat(chat, NOW, "UTC")
        with store._conn:
            store._conn.execute(
                "INSERT INTO quests VALUES (?, ?, ?, ?, NULL)",
                (chat, "2026-11-02", ident, "active"),
            )
    event = store.open_event(1, NOW, NOW + timedelta(minutes=15), "manual", "A", "A")
    quest = next(q for q in QUESTS if q.id == ident)
    assert weekly.current(store, 1, NOW, QUESTS)[2] == 0
    if ident == "weekly-eight":
        for user in range(8):
            store.add_hit(1, event, user, NOW, 1 if user < 7 else 0)
        assert weekly.current(store, 1, NOW, QUESTS)[2] == 7
        store.add_hit(1, event, 8, NOW, 1)
    elif ident == "team-scare":
        for user in range(3):
            store.add_shoo(1, event, user, "a", "b", False, NOW)
    else:
        for user in range(3 if ident == "trio-hit" else 2):
            store.add_hit(1, event, user, NOW, 1)
        if ident == "hit-then-scare":
            assert weekly.current(store, 1, NOW, QUESTS)[2] == 0
            store.add_shoo(1, event, 4, "a", "b", True, NOW)
    weekly.check(store, 1, event, NOW, QUESTS)
    weekly.check(store, 1, event, NOW, QUESTS)
    row, _, value = weekly.current(store, 1, NOW, QUESTS)
    assert value == quest.target and row.status == "completed"
    assert row.completed_event_id == event
    assert weekly.current(store, 2, NOW, QUESTS)[0].status == "active"
    assert store._conn.execute("SELECT COUNT(*) FROM chat_titles").fetchone()[0] == 1
    store.close()
    store = Store(path)
    assert weekly.last_title(store, 1) == quest.title
    store.close()


class Sender:
    def __init__(self):
        self.texts = []
        self.fail = True

    async def send_text(self, chat_id, text):
        self.texts.append(text)
        if self.fail:
            self.fail = False
            raise RuntimeError("retry")


async def test_monday_retry_does_not_draw_fortune(tmp_path):
    store = Store(tmp_path / "db")
    store.enable_chat(1, NOW, "UTC")
    sender = Sender()
    content = load_content(Path("content"))
    clock = FixedClock(NOW + timedelta(hours=4))
    for _ in range(2):
        await fortunes.tick(
            store, clock, random.Random(1), sender, content.fortunes, content.names, QUESTS
        )
    assert len(sender.texts) == 2 and sender.texts[0] == sender.texts[1]
    assert "Новая неделя" in sender.texts[0]
    assert store.get_fortune_deck(1, "daily", 0) is None
    store.close()


def test_deck_cycles_and_migrates_chat(tmp_path):
    store = Store(tmp_path / "db")
    store.enable_chat(1, NOW, "UTC")
    rng = random.Random(7)
    chosen = []
    for week in range(8):
        row = weekly.ensure(store, 1, NOW + timedelta(weeks=week), QUESTS, rng)
        chosen.append(row.quest_id)
    assert len(set(chosen[:4])) == len(set(chosen[4:])) == 4
    assert chosen[3] != chosen[4]
    assert store.migrate_chat(1, 2)
    assert store._conn.execute("SELECT COUNT(*) FROM quests WHERE chat_id=2").fetchone()[0] == 8
    assert (
        store._conn.execute("SELECT COUNT(*) FROM quest_decks WHERE chat_id=2").fetchone()[0] == 1
    )
    assert (
        store._conn.execute("SELECT COUNT(*) FROM quests WHERE status='failed'").fetchone()[0] == 7
    )
    store.close()


async def test_missed_monday_is_not_replayed(tmp_path):
    store = Store(tmp_path / "db")
    store.enable_chat(1, NOW, "UTC")
    sender = Sender()
    sender.fail = False
    content = load_content(Path("content"))
    clock = FixedClock(NOW + timedelta(days=1, hours=4))
    await fortunes.tick(
        store, clock, random.Random(1), sender, content.fortunes, content.names, QUESTS
    )
    assert len(sender.texts) == 1 and "Новая неделя" not in sender.texts[0]
    assert store.get_fortune_deck(1, "daily", 0) is not None
    assert store.get_fortune_issue(1, 0, "2026-11-02") is None
    store.close()


def test_week_ledger_boundaries(tmp_path):
    store = Store(tmp_path / "db")
    store.enable_chat(1, NOW, "Europe/Moscow")
    _, start, end = weekly.bounds(NOW, "Europe/Moscow")
    event = store.open_event(1, NOW, NOW + timedelta(minutes=15), "manual", "A", "A")
    store.upsert_member(1, 1, "Anna")
    store.add_hit(1, event, 1, start, 2)
    store.add_hit(1, event, 2, end, 1)
    assert store.week_balance(1, 1, start, end) == 2
    assert store.week_leaderboard(1, start, end) == [("Anna", 2)]
    store.close()
