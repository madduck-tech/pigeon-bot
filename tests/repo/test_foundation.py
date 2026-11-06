"""Trophy log, chat migration, and upgrades of databases made by older versions."""

import sqlite3
from datetime import UTC, datetime, timedelta, timezone

import pytest

from pigeon.clock import FixedClock
from pigeon.repo.store import SCHEMA, Store
from pigeon.services import game

CHAT = -1001
NEW_CHAT = -100123
TZ = "Europe/Moscow"
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "pigeon.db")
    s.enable_chat(CHAT, NOW, TZ)
    yield s
    s.close()


@pytest.fixture
def clock():
    return FixedClock(NOW)


def test_trophy_time_adds_up_over_several_stretches(store) -> None:
    store.set_trophy(CHAT, 1, "Anna", NOW)
    store.set_trophy(CHAT, 2, "Boris", NOW + timedelta(days=3))
    store.set_trophy(CHAT, 1, "Anna", NOW + timedelta(days=4))
    now = NOW + timedelta(days=6)
    assert store.trophy_held(CHAT, 1, now) == timedelta(days=5)
    assert store.trophy_held(CHAT, 2, now) == timedelta(days=1)


def test_keeping_the_trophy_does_not_split_the_log(store) -> None:
    store.set_trophy(CHAT, 1, "Anna", NOW)
    store.set_trophy(CHAT, 1, "Anna", NOW + timedelta(days=1))
    assert store.trophy_held(CHAT, 1, NOW + timedelta(days=2)) == timedelta(days=2)


def test_migration_moves_everything(store, clock) -> None:
    store.set_adult_enabled(CHAT, True)
    event = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, event.id, 1, "Anna")
    store.add_shoo(CHAT, event.id, 2, "кыш", "сидит", False, NOW)
    store.award(CHAT, 1, "sniper", NOW)

    assert store.migrate_chat(CHAT, NEW_CHAT) is True

    assert store.get_chat(CHAT) is None
    assert store.get_chat(NEW_CHAT).adult_enabled is True
    assert store.balance(NEW_CHAT, 1) == 1
    assert store.get_trophy(NEW_CHAT).holder_user_id == 1
    assert store.current_open_event(NEW_CHAT).id == event.id
    assert store.achievements_of(NEW_CHAT, 1) == ["sniper"]
    assert store.shoo_count(NEW_CHAT, event.id) == 1


def test_migration_leaves_no_row_behind_in_any_table(store, clock, tmp_path) -> None:
    event = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, event.id, 1, "Anna")
    store.migrate_chat(CHAT, NEW_CHAT)
    store.close()
    conn = sqlite3.connect(tmp_path / "pigeon.db")
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in tables:
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        if "chat_id" in columns:
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE chat_id = ?", (CHAT,)
            ).fetchone()[0]
            assert count == 0, table
    conn.close()


def test_migration_keeps_other_chats_untouched(store, clock) -> None:
    other = -1002
    store.enable_chat(other, NOW, TZ)
    event = game.arrive(store, clock, other)
    game.hit(store, clock, other, event.id, 2, "Boris")
    store.migrate_chat(CHAT, NEW_CHAT)
    assert store.balance(other, 2) == 1


def test_migration_is_safe_to_repeat(store) -> None:
    assert store.migrate_chat(CHAT, NEW_CHAT) is True
    assert store.migrate_chat(CHAT, NEW_CHAT) is False
    assert store.get_chat(NEW_CHAT) is not None


def test_migration_refuses_to_merge_into_a_chat_already_set_up(store, clock) -> None:
    store.enable_chat(NEW_CHAT, NOW, TZ)
    event = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, event.id, 1, "Anna")
    assert store.migrate_chat(CHAT, NEW_CHAT) is False
    assert store.balance(CHAT, 1) == 1
    assert not store.is_enabled(CHAT)  # the old id is dead, nothing should keep calling it


def _old_database(path) -> None:
    """A database as M2-darts left it: no pigeon, trophy_log, paused_until."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE chats (
            chat_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
            timezone TEXT NOT NULL DEFAULT 'UTC', quiet_start INTEGER NOT NULL DEFAULT 23,
            quiet_end INTEGER NOT NULL DEFAULT 9, dashboard_message_id INTEGER,
            dashboard_dirty INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
            status TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'manual',
            opened_at TEXT NOT NULL, closes_at TEXT NOT NULL, message_id INTEGER);
        CREATE TABLE trophy (
            chat_id INTEGER PRIMARY KEY, holder_user_id INTEGER NOT NULL,
            holder_name TEXT NOT NULL, since TEXT NOT NULL);
        """
    )
    conn.execute("INSERT INTO chats (chat_id, enabled, created_at) VALUES (?, 1, ?)", (CHAT, "x"))
    conn.execute(
        "INSERT INTO events (chat_id, status, opened_at, closes_at) VALUES (?, 'closed', ?, ?)",
        (CHAT, NOW.isoformat(), NOW.isoformat()),
    )
    conn.execute(
        "INSERT INTO trophy VALUES (?, 1, 'Anna', ?)",
        (CHAT, (NOW - timedelta(days=3)).isoformat()),
    )
    conn.commit()
    conn.close()


def test_old_database_is_upgraded_on_startup(tmp_path) -> None:
    path = tmp_path / "old.db"
    _old_database(path)
    store = Store(path)
    assert store.trophy_held(CHAT, 1, NOW) == timedelta(days=3)
    assert store.get_chat(CHAT).paused_until is None
    assert store.get_chat(CHAT).adult_enabled is False
    assert store.get_event(1).pigeon_name is None
    store.close()


def test_restart_does_not_duplicate_backfilled_rows(tmp_path) -> None:
    path = tmp_path / "old.db"
    _old_database(path)
    Store(path).close()
    store = Store(path)
    assert store.trophy_held(CHAT, 1, NOW) == timedelta(days=3)
    store.close()


def test_schema_is_scoped_by_chat() -> None:
    assert "chat_id" in SCHEMA


def test_timestamps_compare_correctly_whatever_offset_they_arrive_in(store) -> None:
    # Regression: 22:22+03:00 on Sunday sorted after 21:00+00:00 and fell into the next week.
    moscow = timezone(timedelta(hours=3))
    sunday_evening = datetime(2026, 11, 8, 22, 22, tzinfo=moscow)
    store.open_event(CHAT, sunday_evening, sunday_evening + timedelta(hours=1), "auto")
    monday_start_utc = datetime(2026, 11, 8, 21, 0, tzinfo=UTC)
    assert store.count_auto_events_since(CHAT, monday_start_utc) == 0


def _database_before_the_dovecote(path) -> None:
    """A database as the last version of the old bot left it."""
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE members (chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL,"
        " display_name TEXT NOT NULL, username TEXT, PRIMARY KEY (chat_id, user_id));"
        "CREATE TABLE points_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, delta INTEGER NOT NULL,"
        " reason TEXT NOT NULL, ref_id INTEGER, created_at TEXT NOT NULL);"
        "CREATE TABLE achievements (chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL,"
        " code TEXT NOT NULL, awarded_at TEXT NOT NULL, PRIMARY KEY (chat_id, user_id, code));"
        "CREATE TABLE arrival_plans (chat_id INTEGER NOT NULL, local_date TEXT NOT NULL,"
        " planned_at TEXT, status TEXT NOT NULL, postpones INTEGER NOT NULL DEFAULT 0,"
        " PRIMARY KEY (chat_id, local_date));"
        "CREATE TABLE duels (id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL);"
        "INSERT INTO members VALUES (-1, 5, 'Anna', 'anna');"
        "INSERT INTO points_ledger (chat_id, user_id, delta, reason, created_at)"
        " VALUES (-1, 5, 7, 'dart', '2026-01-01T00:00:00+00:00');"
        "INSERT INTO achievements VALUES (-1, 5, 'bullseye', '2026-01-01T00:00:00+00:00');"
        "INSERT INTO duels VALUES (1, -1);"
    )
    old.close()


def test_upgrade_preserves_the_rating_and_forgets_usernames(tmp_path) -> None:
    path = tmp_path / "old.db"
    _database_before_the_dovecote(path)
    store = Store(path)
    assert store.balance(-1, 5) == 7
    assert store.leaderboard(-1) == [("Anna", 7)]
    assert store.member_name(-1, 5) == "Anna"
    assert store.achievements_of(-1, 5) == ["bullseye"]
    store.save_plan(-1, "2026-11-02", NOW, "pending")  # the old table has a postpones column
    store.close()
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT username FROM members").fetchone()[0] is None
    assert conn.execute("SELECT COUNT(*) FROM duels").fetchone()[0] == 1  # not dropped
    conn.close()


def test_rating_is_preserved_on_restart(tmp_path) -> None:
    path = tmp_path / "old.db"
    _database_before_the_dovecote(path)
    store = Store(path)
    store.add_hit(-1, 1, 5, NOW, 1)
    store.close()
    store = Store(path)
    assert store.balance(-1, 5) == 8
    store.close()


def test_migration_moves_rows_of_tables_left_from_closed_mechanics(tmp_path) -> None:
    path = tmp_path / "old.db"
    _database_before_the_dovecote(path)
    store = Store(path)
    store.enable_chat(-1, NOW, TZ)
    assert store.migrate_chat(-1, -2) is True
    store.close()
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT chat_id FROM duels").fetchone()[0] == -2
    conn.close()
