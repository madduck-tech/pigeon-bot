"""SQLite storage. Every query is scoped by chat_id."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id      INTEGER PRIMARY KEY,
    enabled      INTEGER NOT NULL DEFAULT 0,
    adult_enabled INTEGER NOT NULL DEFAULT 0,
    timezone     TEXT NOT NULL DEFAULT 'UTC',
    quiet_start  INTEGER NOT NULL DEFAULT 23,
    quiet_end    INTEGER NOT NULL DEFAULT 9,
    paused_until TEXT,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    chat_id      INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);
-- pigeon_name and intro are NULL on arrivals opened before pigeons had their own names.
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    source      TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'auto')),
    pigeon_name TEXT,
    intro       TEXT,
    opened_at   TEXT NOT NULL,
    closes_at   TEXT NOT NULL,
    message_id  INTEGER
);
CREATE INDEX IF NOT EXISTS events_chat_status ON events (chat_id, status);
CREATE TABLE IF NOT EXISTS pigeon_decks (
    chat_id INTEGER PRIMARY KEY, remaining_json TEXT NOT NULL, last_type TEXT
);
CREATE TABLE IF NOT EXISTS hits (
    event_id   INTEGER NOT NULL REFERENCES events (id),
    chat_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (event_id, user_id)
);
-- action and reaction are lines from content/shoos.yaml, kept because the message is re-rendered.
CREATE TABLE IF NOT EXISTS shoos (
    event_id   INTEGER NOT NULL REFERENCES events (id),
    chat_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    action     TEXT NOT NULL,
    reaction   TEXT NOT NULL,
    flew       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (event_id, user_id)
);
CREATE TABLE IF NOT EXISTS action_order (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, event_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL, kind TEXT NOT NULL, UNIQUE(event_id, user_id, kind)
);
CREATE TABLE IF NOT EXISTS points_ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    delta      INTEGER NOT NULL,
    reason     TEXT NOT NULL,
    ref_id     INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS arrival_plans (
    chat_id    INTEGER NOT NULL,
    local_date TEXT NOT NULL,
    planned_at TEXT,
    status     TEXT NOT NULL CHECK (status IN ('pending', 'fired', 'skipped')),
    PRIMARY KEY (chat_id, local_date)
);
CREATE TABLE IF NOT EXISTS trophy (
    chat_id        INTEGER PRIMARY KEY,
    holder_user_id INTEGER NOT NULL,
    holder_name    TEXT NOT NULL,
    since          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trophy_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at   TEXT
);
CREATE INDEX IF NOT EXISTS trophy_log_chat_user ON trophy_log (chat_id, user_id);
CREATE TABLE IF NOT EXISTS achievements (
    chat_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    code       TEXT NOT NULL,
    awarded_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, user_id, code)
);
CREATE TABLE IF NOT EXISTS fortunes (
    chat_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    local_day TEXT NOT NULL,
    PRIMARY KEY (chat_id, user_id, local_day)
);
CREATE TABLE IF NOT EXISTS fortune_decks (
    chat_id INTEGER NOT NULL, scope TEXT NOT NULL, owner_id INTEGER NOT NULL,
    cycle INTEGER NOT NULL, remaining_json TEXT NOT NULL, forced_json TEXT NOT NULL,
    last_fortune_id TEXT, known_json TEXT NOT NULL, PRIMARY KEY (chat_id, scope, owner_id)
);
CREATE TABLE IF NOT EXISTS quests (
    chat_id INTEGER NOT NULL, week_key TEXT NOT NULL, quest_id TEXT NOT NULL,
    status TEXT NOT NULL, completed_event_id INTEGER, PRIMARY KEY(chat_id, week_key)
);
CREATE TABLE IF NOT EXISTS quest_decks (
    chat_id INTEGER PRIMARY KEY, remaining_json TEXT NOT NULL, last_quest_id TEXT
);
CREATE TABLE IF NOT EXISTS chat_titles (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, quest_id TEXT NOT NULL,
    title TEXT NOT NULL, week_key TEXT NOT NULL, awarded_at TEXT NOT NULL,
    UNIQUE(chat_id, week_key)
);
CREATE TABLE IF NOT EXISTS daily_fortunes (
    chat_id   INTEGER NOT NULL,
    local_day TEXT NOT NULL,
    PRIMARY KEY (chat_id, local_day)
);
CREATE TABLE IF NOT EXISTS courier_plans (
    chat_id INTEGER NOT NULL, local_date TEXT NOT NULL, planned_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'fired', 'skipped')),
    PRIMARY KEY (chat_id, local_date)
);
CREATE TABLE IF NOT EXISTS courier_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
    fortune_id TEXT NOT NULL, rendered_text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS courier_fortunes (
    chat_id INTEGER NOT NULL, local_day TEXT NOT NULL, fortune_id TEXT NOT NULL,
    rendered_text TEXT NOT NULL, sent_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, local_day)
);
CREATE TABLE IF NOT EXISTS content_migrations (name TEXT PRIMARY KEY);
-- The weekly chronicle (ADR-0014). week_key is the Monday of the post; text is set when ready.
CREATE TABLE IF NOT EXISTS chronicles (
    chat_id    INTEGER NOT NULL,
    week_key   TEXT NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('pending', 'ready', 'empty', 'failed')),
    text       TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, week_key)
);
"""

# Columns added after the first schema. Databases created earlier get them on startup.
MIGRATIONS = [
    ("chats", "adult_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ("chats", "timezone", "TEXT NOT NULL DEFAULT 'UTC'"),
    ("chats", "quiet_start", "INTEGER NOT NULL DEFAULT 23"),
    ("chats", "quiet_end", "INTEGER NOT NULL DEFAULT 9"),
    ("chats", "paused_until", "TEXT"),
    ("events", "source", "TEXT NOT NULL DEFAULT 'manual'"),
    ("events", "pigeon_name", "TEXT"),
    ("events", "intro", "TEXT"),
    ("events", "pigeon_type", "TEXT NOT NULL DEFAULT 'normal'"),
    ("events", "type_state", "TEXT NOT NULL DEFAULT '{}'"),
    ("hits", "outcome", "TEXT NOT NULL DEFAULT 'normal'"),
    ("hits", "points", "INTEGER NOT NULL DEFAULT 1"),
    ("hits", "reaction", "TEXT NOT NULL DEFAULT 'Попал!'"),
    ("fortunes", "fortune_id", "TEXT"),
    ("fortunes", "rendered_text", "TEXT"),
    ("daily_fortunes", "fortune_id", "TEXT"),
    ("daily_fortunes", "rendered_text", "TEXT"),
    ("daily_fortunes", "sent_at", "TEXT"),
]

# Rows for databases created before these tables existed. Safe to run on every start.
BACKFILL = """
INSERT INTO trophy_log (chat_id, user_id, started_at)
    SELECT chat_id, holder_user_id, since FROM trophy
    WHERE chat_id NOT IN (SELECT chat_id FROM trophy_log);
"""

DOVECOTE_VERSION = 1


@dataclass(frozen=True)
class PigeonDeck:
    remaining: list[str]
    last_type: str | None


@dataclass(frozen=True)
class QuestRecord:
    chat_id: int
    week_key: str
    quest_id: str
    status: str
    completed_event_id: int | None


@dataclass(frozen=True)
class QuestDeck:
    remaining: list[str]
    last_quest_id: str | None


@dataclass(frozen=True)
class FortuneDeck:
    cycle: int
    remaining: list[str]
    forced: list[str]
    last_fortune_id: str | None
    known: list[str]


@dataclass(frozen=True)
class FortuneIssue:
    fortune_id: str | None
    rendered_text: str | None
    sent_at: str | None = None


@dataclass(frozen=True)
class Chronicle:
    status: str
    text: str | None


@dataclass(frozen=True)
class ChroniclePlayer:
    name: str
    points: int
    hits: int
    misses: int
    big_hits: int
    shoo_attempts: int
    scared_away: int


@dataclass(frozen=True)
class ChronicleWeek:
    arrivals_by_type: dict[str, int]
    scared_away: int
    players: list[ChroniclePlayer]


@dataclass(frozen=True)
class ChatSettings:
    chat_id: int
    timezone: str
    quiet_start: int
    quiet_end: int
    paused_until: datetime | None = None
    adult_enabled: bool = False


@dataclass(frozen=True)
class Event:
    id: int
    chat_id: int
    status: str
    source: str
    pigeon_name: str | None
    intro: str | None
    opened_at: datetime
    closes_at: datetime
    message_id: int | None
    pigeon_type: str = "normal"
    type_state: str = "{}"


@dataclass(frozen=True)
class Shoo:
    name: str
    action: str
    reaction: str
    flew: bool


@dataclass(frozen=True)
class ArrivalPlan:
    chat_id: int
    local_date: str
    planned_at: datetime | None
    status: str


@dataclass(frozen=True)
class Trophy:
    chat_id: int
    holder_user_id: int
    holder_name: str
    since: datetime


def _ts(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    # Always UTC: SQL compares these as strings, which is only valid at a single offset.
    return value.astimezone(UTC).isoformat()


def _opt_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _event(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"],
        chat_id=row["chat_id"],
        status=row["status"],
        source=row["source"],
        pigeon_name=row["pigeon_name"],
        intro=row["intro"],
        opened_at=datetime.fromisoformat(row["opened_at"]),
        closes_at=datetime.fromisoformat(row["closes_at"]),
        message_id=row["message_id"],
        pigeon_type=row["pigeon_type"],
        type_state=row["type_state"],
    )


def _chat(row: sqlite3.Row) -> ChatSettings:
    return ChatSettings(
        chat_id=row["chat_id"],
        timezone=row["timezone"],
        quiet_start=row["quiet_start"],
        quiet_end=row["quiet_end"],
        paused_until=_opt_dt(row["paused_until"]),
        adult_enabled=bool(row["adult_enabled"]),
    )


class Store:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        old_daily = "sent_at" not in self._columns("daily_fortunes")
        for table, column, decl in MIGRATIONS:
            self._ensure_column(table, column, decl)
        if old_daily:
            with self._conn:
                self._conn.execute("UPDATE daily_fortunes SET sent_at = local_day")
        self._conn.executescript(BACKFILL)
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO action_order (chat_id, event_id, user_id, kind) "
                "SELECT chat_id, event_id, user_id, kind FROM ("
                "SELECT chat_id, event_id, user_id, 'hit' AS kind, created_at FROM hits "
                "UNION ALL SELECT chat_id, event_id, user_id, 'shoo', created_at FROM shoos) "
                "ORDER BY created_at"
            )
        # Preserve all existing ledger entries during upgrades.
        if self._conn.execute("PRAGMA user_version").fetchone()[0] < DOVECOTE_VERSION:
            if "username" in self._columns("members"):
                self._conn.execute("UPDATE members SET username = NULL")
            self._conn.execute(f"PRAGMA user_version = {DOVECOTE_VERSION}")
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _columns(self, table: str) -> set[str]:
        return {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}

    def _ensure_column(self, table: str, column: str, decl: str) -> None:
        if column not in self._columns(table):
            with self._conn:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def _become_dovecote(self) -> None:
        """One-time upgrade: the rating restarts, and usernames are no longer kept.

        The ledger held points of closed mechanics, so from here on it counts hits only.
        """
        if self._conn.execute("PRAGMA user_version").fetchone()[0] >= DOVECOTE_VERSION:
            return
        with self._conn:
            self._conn.execute("DELETE FROM points_ledger")
            if "username" in self._columns("members"):
                self._conn.execute("UPDATE members SET username = NULL")
            self._conn.execute(f"PRAGMA user_version = {DOVECOTE_VERSION}")

    # chats
    def enable_chat(self, chat_id: int, now: datetime, timezone: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO chats (chat_id, enabled, timezone, created_at) VALUES (?, 1, ?, ?) "
                "ON CONFLICT (chat_id) DO UPDATE SET enabled = 1, timezone = excluded.timezone",
                (chat_id, timezone, _ts(now)),
            )

    def migrate_chat(self, old_chat_id: int, new_chat_id: int) -> bool:
        """Move a chat to its new id after a supergroup upgrade.

        Returns False if there is nothing to move, since Telegram can announce one migration
        twice, or if the new id is already set up. Two chats are never merged: the old id is
        dead by then, so it is only switched off, which also stops every retry aimed at it.
        Tables are found from the schema, so a new table cannot be forgotten here.
        """
        if self.get_chat(old_chat_id) is None:
            return False
        if self.get_chat(new_chat_id) is not None:
            with self._conn:
                self._conn.execute("UPDATE chats SET enabled = 0 WHERE chat_id = ?", (old_chat_id,))
            return False
        tables = [
            row["name"]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        with self._conn:
            for table in tables:
                if "chat_id" in self._columns(table):
                    self._conn.execute(
                        f"UPDATE {table} SET chat_id = ? WHERE chat_id = ?",
                        (new_chat_id, old_chat_id),
                    )
        return True

    def set_paused_until(self, chat_id: int, until: datetime | None) -> None:
        value = _ts(until) if until else None
        with self._conn:
            self._conn.execute(
                "UPDATE chats SET paused_until = ? WHERE chat_id = ?", (value, chat_id)
            )

    def set_quiet_hours(self, chat_id: int, start: int, end: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE chats SET quiet_start = ?, quiet_end = ? WHERE chat_id = ?",
                (start, end, chat_id),
            )

    def set_adult_enabled(self, chat_id: int, enabled: bool) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE chats SET adult_enabled = ? WHERE chat_id = ?", (int(enabled), chat_id)
            )

    def get_chat(self, chat_id: int) -> ChatSettings | None:
        row = self._conn.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        return _chat(row) if row else None

    def disable_chat(self, chat_id: int) -> None:
        """Stop automatic work without deleting the chat's history or settings."""
        with self._conn:
            self._conn.execute("UPDATE chats SET enabled = 0 WHERE chat_id = ?", (chat_id,))

    def is_enabled(self, chat_id: int) -> bool:
        row = self._conn.execute(
            "SELECT enabled FROM chats WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return bool(row and row["enabled"])

    def enabled_chats(self) -> list[ChatSettings]:
        rows = self._conn.execute(
            "SELECT * FROM chats WHERE enabled = 1 ORDER BY chat_id"
        ).fetchall()
        return [_chat(row) for row in rows]

    # members
    def upsert_member(self, chat_id: int, user_id: int, display_name: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO members (chat_id, user_id, display_name) VALUES (?, ?, ?) "
                "ON CONFLICT (chat_id, user_id) DO UPDATE SET display_name = excluded.display_name",
                (chat_id, user_id, display_name),
            )

    def member_name(self, chat_id: int, user_id: int) -> str | None:
        row = self._conn.execute(
            "SELECT display_name FROM members WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ).fetchone()
        return row["display_name"] if row else None

    # trophy
    def get_trophy(self, chat_id: int) -> Trophy | None:
        row = self._conn.execute("SELECT * FROM trophy WHERE chat_id = ?", (chat_id,)).fetchone()
        if row is None:
            return None
        return Trophy(
            row["chat_id"],
            row["holder_user_id"],
            row["holder_name"],
            datetime.fromisoformat(row["since"]),
        )

    def set_trophy(self, chat_id: int, user_id: int, name: str, now: datetime) -> bool:
        """Give the trophy to user_id. Returns True if the holder changed."""
        current = self.get_trophy(chat_id)
        if current is not None and current.holder_user_id == user_id:
            with self._conn:
                self._conn.execute(
                    "UPDATE trophy SET holder_name = ? WHERE chat_id = ?", (name, chat_id)
                )
            return False
        with self._conn:
            self._conn.execute(
                "UPDATE trophy_log SET ended_at = ? WHERE chat_id = ? AND ended_at IS NULL",
                (_ts(now), chat_id),
            )
            self._conn.execute(
                "INSERT INTO trophy_log (chat_id, user_id, started_at) VALUES (?, ?, ?)",
                (chat_id, user_id, _ts(now)),
            )
            self._conn.execute(
                "INSERT INTO trophy (chat_id, holder_user_id, holder_name, since) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (chat_id) DO UPDATE SET "
                "holder_user_id = excluded.holder_user_id, holder_name = excluded.holder_name, "
                "since = excluded.since",
                (chat_id, user_id, name, _ts(now)),
            )
        return True

    def trophy_held(self, chat_id: int, user_id: int, now: datetime) -> timedelta:
        """Total time this user has held the trophy, including the current stretch."""
        rows = self._conn.execute(
            "SELECT started_at, ended_at FROM trophy_log WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ).fetchall()
        return sum(
            (
                (_opt_dt(row["ended_at"]) or now) - datetime.fromisoformat(row["started_at"])
                for row in rows
            ),
            timedelta(),
        )

    def trophy_user_ids(self, chat_id: int) -> set[int]:
        rows = self._conn.execute(
            "SELECT DISTINCT user_id FROM trophy_log WHERE chat_id = ?", (chat_id,)
        ).fetchall()
        return {row["user_id"] for row in rows}

    # events
    def get_pigeon_deck(self, chat_id: int) -> PigeonDeck | None:
        row = self._conn.execute(
            "SELECT remaining_json, last_type FROM pigeon_decks WHERE chat_id=?", (chat_id,)
        ).fetchone()
        return PigeonDeck(json.loads(row["remaining_json"]), row["last_type"]) if row else None

    def save_pigeon_deck(self, chat_id: int, deck: PigeonDeck) -> None:
        with self._write():
            self._conn.execute(
                "INSERT OR REPLACE INTO pigeon_decks VALUES (?, ?, ?)",
                (chat_id, json.dumps(deck.remaining), deck.last_type),
            )

    def previous_pigeon_type(self, chat_id: int, event_id: int) -> str | None:
        row = self._conn.execute(
            "SELECT pigeon_type FROM events WHERE chat_id=? AND id<? ORDER BY id DESC LIMIT 1",
            (chat_id, event_id),
        ).fetchone()
        return row["pigeon_type"] if row else None

    def open_event(
        self,
        chat_id: int,
        now: datetime,
        closes_at: datetime,
        source: str = "manual",
        pigeon_name: str | None = None,
        intro: str | None = None,
        pigeon_type: str = "normal",
        type_state: str = "{}",
    ) -> int:
        with self._write():
            cur = self._conn.execute(
                "INSERT INTO events "
                "(chat_id, status, source, pigeon_name, intro, opened_at, closes_at, "
                "pigeon_type, type_state) VALUES (?, 'open', ?, ?, ?, ?, ?, ?, ?)",
                (
                    chat_id,
                    source,
                    pigeon_name,
                    intro,
                    _ts(now),
                    _ts(closes_at),
                    pigeon_type,
                    type_state,
                ),
            )
        return int(cur.lastrowid)

    def update_event_type_state(self, chat_id: int, event_id: int, state: str) -> None:
        with self._write():
            self._conn.execute(
                "UPDATE events SET type_state = ? WHERE chat_id = ? AND id = ?",
                (state, chat_id, event_id),
            )

    def get_event(self, event_id: int) -> Event | None:
        row = self._conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return _event(row) if row else None

    def current_open_event(self, chat_id: int) -> Event | None:
        row = self._conn.execute(
            "SELECT * FROM events WHERE chat_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
        return _event(row) if row else None

    def event_by_message(self, chat_id: int, message_id: int) -> Event | None:
        row = self._conn.execute(
            "SELECT * FROM events WHERE chat_id = ? AND message_id = ?", (chat_id, message_id)
        ).fetchone()
        return _event(row) if row else None

    def delete_unsent_event(self, chat_id: int, event_id: int) -> bool:
        """Remove an arrival whose send failed, as if it never happened.

        Returns False and keeps it if anyone already acted on it: then the message did reach
        the chat and only Telegram's response was lost.
        """
        with self._write():
            for table in ("hits", "shoos"):
                acted = self._conn.execute(
                    f"SELECT 1 FROM {table} WHERE event_id = ? AND chat_id = ? LIMIT 1",
                    (event_id, chat_id),
                ).fetchone()
                if acted:
                    return False
            self._conn.execute(
                "DELETE FROM events WHERE id = ? AND chat_id = ?", (event_id, chat_id)
            )
        return True

    def close_event(self, event_id: int) -> None:
        with self._write():
            self._conn.execute("UPDATE events SET status = 'closed' WHERE id = ?", (event_id,))

    def set_event_message(self, event_id: int, message_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE events SET message_id = ? WHERE id = ?", (message_id, event_id)
            )

    def count_auto_events_since(self, chat_id: int, since: datetime) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE chat_id = ? AND source = 'auto' "
            "AND opened_at >= ?",
            (chat_id, _ts(since)),
        ).fetchone()
        return int(row["n"])

    def arrivals_total(self, chat_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return int(row["n"])

    def hit_count(self, event_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM hits WHERE event_id = ? AND points > 0", (event_id,)
        ).fetchone()
        return int(row["n"])

    # arrival plans (one per chat per local day)
    def get_plan(self, chat_id: int, local_date: str) -> ArrivalPlan | None:
        row = self._conn.execute(
            "SELECT * FROM arrival_plans WHERE chat_id = ? AND local_date = ?",
            (chat_id, local_date),
        ).fetchone()
        if row is None:
            return None
        planned = datetime.fromisoformat(row["planned_at"]) if row["planned_at"] else None
        return ArrivalPlan(row["chat_id"], row["local_date"], planned, row["status"])

    def save_plan(
        self, chat_id: int, local_date: str, planned_at: datetime | None, status: str
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO arrival_plans (chat_id, local_date, planned_at, status) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (chat_id, local_date) DO UPDATE SET "
                "planned_at = excluded.planned_at, status = excluded.status",
                (chat_id, local_date, _ts(planned_at) if planned_at else None, status),
            )

    # hits and points
    def add_hit(
        self,
        chat_id: int,
        event_id: int,
        user_id: int,
        now: datetime,
        points: int,
        outcome: str = "normal",
        reaction: str = "Попал!",
    ) -> bool:
        """Record a hit and its points atomically. Returns False if the user already hit."""
        with self._write():
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO hits "
                "(event_id, chat_id, user_id, created_at, points, outcome, reaction) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_id, chat_id, user_id, _ts(now), points, outcome, reaction),
            )
            if cur.rowcount == 0:
                return False
            self._conn.execute(
                "INSERT INTO action_order (chat_id, event_id, user_id, kind) "
                "VALUES (?, ?, ?, 'hit')",
                (chat_id, event_id, user_id),
            )
            if outcome == "cap_knocked":
                self._conn.execute(
                    "UPDATE events SET type_state=? WHERE id=?", ('{"cap_knocked": true}', event_id)
                )
            if points > 0:
                self._conn.execute(
                    "INSERT INTO points_ledger "
                    "(chat_id, user_id, delta, reason, ref_id, created_at) "
                    "VALUES (?, ?, ?, 'hit', ?, ?)",
                    (chat_id, user_id, points, event_id, _ts(now)),
                )
        return True

    def balance(self, chat_id: int, user_id: int) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(delta), 0) AS n FROM points_ledger "
            "WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ).fetchone()
        return int(row["n"])

    def leaderboard(self, chat_id: int, limit: int = 10) -> list[tuple[str, int]]:
        rows = self._conn.execute(
            "SELECT COALESCE(m.display_name, '?') AS name, SUM(l.delta) AS total "
            "FROM points_ledger AS l "
            "LEFT JOIN members AS m ON m.chat_id = l.chat_id AND m.user_id = l.user_id "
            "WHERE l.chat_id = ? GROUP BY l.user_id ORDER BY total DESC, name ASC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [(row["name"], int(row["total"])) for row in rows]

    # shoos
    def add_shoo(
        self,
        chat_id: int,
        event_id: int,
        user_id: int,
        action: str,
        reaction: str,
        flew: bool,
        now: datetime,
    ) -> bool:
        """Record an attempt to shoo the pigeon. False if the user already tried on this one."""
        with self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO shoos "
                "(event_id, chat_id, user_id, action, reaction, flew, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_id, chat_id, user_id, action, reaction, int(flew), _ts(now)),
            )
            if cur.rowcount:
                self._conn.execute(
                    "INSERT INTO action_order (chat_id, event_id, user_id, kind) "
                    "VALUES (?, ?, ?, 'shoo')",
                    (chat_id, event_id, user_id),
                )
        return cur.rowcount > 0

    def shoo_count(self, chat_id: int, event_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM shoos WHERE chat_id = ? AND event_id = ?",
            (chat_id, event_id),
        ).fetchone()
        return int(row["n"])

    def shoos_of(self, chat_id: int, event_id: int) -> list[Shoo]:
        """This arrival's attempts, oldest first."""
        rows = self._conn.execute(
            "SELECT COALESCE(m.display_name, '?') AS name, s.action, s.reaction, s.flew "
            "FROM shoos AS s "
            "LEFT JOIN members AS m ON m.chat_id = s.chat_id AND m.user_id = s.user_id "
            "WHERE s.chat_id = ? AND s.event_id = ? ORDER BY s.rowid",
            (chat_id, event_id),
        ).fetchall()
        return [
            Shoo(row["name"], row["action"], row["reaction"], bool(row["flew"])) for row in rows
        ]

    def actions_of(self, chat_id: int, event_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT a.id, a.kind, COALESCE(m.display_name, '?') AS name, "
            "COALESCE(h.reaction, s.reaction) AS reaction, COALESCE(h.points, 0) AS points, "
            "COALESCE(s.action, '') AS action FROM action_order a "
            "LEFT JOIN members m ON m.chat_id=a.chat_id AND m.user_id=a.user_id "
            "LEFT JOIN hits h ON a.kind='hit' AND h.event_id=a.event_id AND h.user_id=a.user_id "
            "LEFT JOIN shoos s ON a.kind='shoo' AND s.event_id=a.event_id AND s.user_id=a.user_id "
            "WHERE a.chat_id=? AND a.event_id=? ORDER BY a.id",
            (chat_id, event_id),
        ).fetchall()
        return [dict(row) for row in rows][-3:]

    def hit_result(self, chat_id: int, event_id: int, user_id: int) -> tuple[str, int]:
        row = self._conn.execute(
            "SELECT reaction, points FROM hits WHERE chat_id=? AND event_id=? AND user_id=?",
            (chat_id, event_id, user_id),
        ).fetchone()
        return row["reaction"], row["points"]

    def scares(self, chat_id: int, user_id: int) -> int:
        """How many pigeons this user has scared off."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM shoos WHERE chat_id = ? AND user_id = ? AND flew = 1",
            (chat_id, user_id),
        ).fetchone()
        return int(row["n"])

    # counts for achievements
    def hits_in_last_arrivals(self, chat_id: int, user_id: int, count: int) -> int:
        """How many of the chat's last `count` arrivals this user hit."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM hits WHERE chat_id = ? AND user_id = ? "
            "AND points > 0 AND event_id IN "
            "(SELECT id FROM events WHERE chat_id = ? ORDER BY id DESC LIMIT ?)",
            (chat_id, user_id, chat_id, count),
        ).fetchone()
        return int(row["n"])

    def first_hits(self, chat_id: int, user_id: int) -> int:
        """On how many arrivals this user was the first to hit."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM hits AS h WHERE h.chat_id = ? AND h.user_id = ? "
            "AND h.points > 0 "
            "AND h.rowid = (SELECT MIN(rowid) FROM hits WHERE event_id = h.event_id "
            "AND points > 0)",
            (chat_id, user_id),
        ).fetchone()
        return int(row["n"])

    # achievements
    def award(self, chat_id: int, user_id: int, code: str, now: datetime) -> bool:
        """Grant an achievement. False if the user already has it."""
        with self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO achievements (chat_id, user_id, code, awarded_at) "
                "VALUES (?, ?, ?, ?)",
                (chat_id, user_id, code, _ts(now)),
            )
        return cur.rowcount > 0

    def achievements_of(self, chat_id: int, user_id: int) -> list[str]:
        rows = self._conn.execute(
            "SELECT code FROM achievements WHERE chat_id = ? AND user_id = ? ORDER BY awarded_at",
            (chat_id, user_id),
        ).fetchall()
        return [row["code"] for row in rows]

    # fortunes
    @contextmanager
    def _write(self) -> Iterator[None]:
        """Commit standalone writes, or join an existing service transaction."""
        if self._conn.in_transaction:
            yield
        else:
            with self._conn:
                yield

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Serialize a read-modify-write operation and commit or roll it back together."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def fortune_transaction(self):
        """Compatibility alias for the fortune service."""
        return self.transaction()

    def get_fortune_issue(self, chat_id: int, owner_id: int, local_day: str) -> FortuneIssue | None:
        if owner_id == 0:
            row = self._conn.execute(
                "SELECT fortune_id, rendered_text, sent_at FROM daily_fortunes "
                "WHERE chat_id=? AND local_day=?",
                (chat_id, local_day),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT fortune_id, rendered_text FROM fortunes "
                "WHERE chat_id=? AND user_id=? AND local_day=?",
                (chat_id, owner_id, local_day),
            ).fetchone()
        if row is None:
            return None
        return FortuneIssue(
            row["fortune_id"], row["rendered_text"], row["sent_at"] if owner_id == 0 else None
        )

    def get_fortune_deck(self, chat_id: int, scope: str, owner_id: int) -> FortuneDeck | None:
        row = self._conn.execute(
            "SELECT * FROM fortune_decks WHERE chat_id=? AND scope=? AND owner_id=?",
            (chat_id, scope, owner_id),
        ).fetchone()
        if row is None:
            return None
        return FortuneDeck(
            row["cycle"],
            json.loads(row["remaining_json"]),
            json.loads(row["forced_json"]),
            row["last_fortune_id"],
            json.loads(row["known_json"]),
        )

    def fortune_history(self, chat_id: int, start: str, end: str) -> set[str]:
        ids: set[str] = set()
        for table in ("fortunes", "daily_fortunes", "courier_fortunes"):
            ids.update(
                row[0]
                for row in self._conn.execute(
                    f"SELECT fortune_id FROM {table} WHERE chat_id=? "
                    "AND local_day BETWEEN ? AND ? AND fortune_id IS NOT NULL",
                    (chat_id, start, end),
                )
            )
        return ids

    def save_fortune_deck(self, chat_id: int, scope: str, owner_id: int, deck: FortuneDeck) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO fortune_decks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chat_id,
                scope,
                owner_id,
                deck.cycle,
                json.dumps(deck.remaining),
                json.dumps(deck.forced),
                deck.last_fortune_id,
                json.dumps(deck.known),
            ),
        )

    def save_fortune_issue(
        self,
        chat_id: int,
        owner_id: int,
        local_day: str,
        fortune_id: str | None,
        rendered_text: str,
    ) -> None:
        if owner_id == 0:
            self._conn.execute(
                "INSERT INTO daily_fortunes (chat_id, local_day, fortune_id, rendered_text) "
                "VALUES (?, ?, ?, ?)",
                (chat_id, local_day, fortune_id, rendered_text),
            )
        else:
            self._conn.execute(
                "INSERT INTO fortunes (chat_id, user_id, local_day, fortune_id, rendered_text) "
                "VALUES (?, ?, ?, ?, ?)",
                (chat_id, owner_id, local_day, fortune_id, rendered_text),
            )

    def mark_daily_fortune_sent(self, chat_id: int, local_day: str, now: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE daily_fortunes SET sent_at=? WHERE chat_id=? AND local_day=?",
                (_ts(now), chat_id, local_day),
            )

    # courier: reservations are not published history
    def courier_migrated(self) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM content_migrations WHERE name='courier-v1'"
            ).fetchone()
            is not None
        )

    def legacy_pending_fortunes(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT chat_id, local_day, fortune_id, rendered_text FROM daily_fortunes "
            "WHERE sent_at IS NULL AND fortune_id IS NOT NULL ORDER BY local_day, chat_id"
        ).fetchall()

    def upgrade_courier(self, pending: list[tuple[int, str, str]]) -> None:
        """Called in the migration transaction; pending texts have been rendered by the service."""
        for chat_id, ident, rendered in pending:
            self.enqueue_courier(chat_id, ident, rendered)
        self._conn.execute(
            "DELETE FROM daily_fortunes WHERE sent_at IS NULL AND fortune_id IS NOT NULL"
        )
        self._conn.execute("UPDATE fortune_decks SET forced_json='[]'")
        self._conn.execute("INSERT INTO content_migrations VALUES ('courier-v1')")

    def courier_plan(self, chat_id: int, local_day: str) -> ArrivalPlan | None:
        row = self._conn.execute(
            "SELECT * FROM courier_plans WHERE chat_id=? AND local_date=?", (chat_id, local_day)
        ).fetchone()
        return (
            ArrivalPlan(chat_id, local_day, _opt_dt(row["planned_at"]), row["status"])
            if row
            else None
        )

    def save_courier_plan(
        self, chat_id: int, day: str, planned_at: datetime | None, status: str
    ) -> None:
        with self._write():
            self._conn.execute(
                "INSERT INTO courier_plans VALUES (?, ?, ?, ?) "
                "ON CONFLICT(chat_id, local_date) DO UPDATE SET "
                "planned_at=excluded.planned_at, status=excluded.status",
                (chat_id, day, _ts(planned_at) if planned_at else None, status),
            )

    def courier_count(self, chat_id: int, week_key: str, next_week: str) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM courier_fortunes "
            "WHERE chat_id=? AND local_day>=? AND local_day<?",
            (chat_id, week_key, next_week),
        ).fetchone()[0]

    def courier_published(self, chat_id: int, local_day: str) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM courier_fortunes WHERE chat_id=? AND local_day=?",
                (chat_id, local_day),
            ).fetchone()
            is not None
        )

    def pending_courier(self, chat_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM courier_queue WHERE chat_id=? ORDER BY id LIMIT 1", (chat_id,)
        ).fetchone()

    def enqueue_courier(self, chat_id: int, ident: str, rendered: str) -> None:
        self._conn.execute(
            "INSERT INTO courier_queue (chat_id, fortune_id, rendered_text) VALUES (?, ?, ?)",
            (chat_id, ident, rendered),
        )

    def confirm_courier(
        self, chat_id: int, day: str, published_day: str, queue_id: int, now: datetime
    ) -> None:
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM courier_queue WHERE chat_id=? AND id=?", (chat_id, queue_id)
            ).fetchone()
            if row is None:
                raise ValueError("courier reservation missing")
            self._conn.execute(
                "INSERT INTO courier_fortunes VALUES (?, ?, ?, ?, ?)",
                (chat_id, published_day, row["fortune_id"], row["rendered_text"], _ts(now)),
            )
            self._conn.execute(
                "DELETE FROM courier_queue WHERE chat_id=? AND id=?", (chat_id, queue_id)
            )
            plan = self.courier_plan(chat_id, day)
            self.save_courier_plan(chat_id, day, plan.planned_at if plan else None, "fired")

    def week_leaderboard(
        self, chat_id: int, start: datetime, end: datetime, limit: int = 10
    ) -> list[tuple[str, int]]:
        rows = self._conn.execute(
            "SELECT COALESCE(m.display_name, '?') AS name, SUM(l.delta) AS total "
            "FROM points_ledger l LEFT JOIN members m "
            "ON m.chat_id=l.chat_id AND m.user_id=l.user_id "
            "WHERE l.chat_id=? AND l.created_at>=? AND l.created_at<? "
            "GROUP BY l.user_id ORDER BY total DESC, name ASC LIMIT ?",
            (chat_id, _ts(start), _ts(end), limit),
        ).fetchall()
        return [(r["name"], int(r["total"])) for r in rows]

    def week_balance(self, chat_id: int, user_id: int, start: datetime, end: datetime) -> int:
        return int(
            self._conn.execute(
                "SELECT COALESCE(SUM(delta),0) FROM points_ledger WHERE chat_id=? AND user_id=? "
                "AND created_at>=? AND created_at<?",
                (chat_id, user_id, _ts(start), _ts(end)),
            ).fetchone()[0]
        )

    # Weekly quests: write methods join an enclosing service transaction when present.
    def get_quest(self, chat_id: int, week_key: str) -> QuestRecord | None:
        row = self._conn.execute(
            "SELECT * FROM quests WHERE chat_id=? AND week_key=?", (chat_id, week_key)
        ).fetchone()
        return QuestRecord(**dict(row)) if row else None

    def fail_previous_quests(self, chat_id: int, week_key: str) -> None:
        with self._write():
            self._conn.execute(
                "UPDATE quests SET status='failed' "
                "WHERE chat_id=? AND week_key<? AND status='active'",
                (chat_id, week_key),
            )

    def create_quest(self, chat_id: int, week_key: str, quest_id: str) -> QuestRecord:
        with self._write():
            self._conn.execute(
                "INSERT INTO quests VALUES (?,?,?,'active',NULL)", (chat_id, week_key, quest_id)
            )
        return QuestRecord(chat_id, week_key, quest_id, "active", None)

    def get_quest_deck(self, chat_id: int) -> QuestDeck | None:
        row = self._conn.execute(
            "SELECT remaining_json, last_quest_id FROM quest_decks WHERE chat_id=?", (chat_id,)
        ).fetchone()
        return QuestDeck(json.loads(row["remaining_json"]), row["last_quest_id"]) if row else None

    def save_quest_deck(self, chat_id: int, deck: QuestDeck) -> None:
        with self._write():
            self._conn.execute(
                "INSERT OR REPLACE INTO quest_decks VALUES (?,?,?)",
                (chat_id, json.dumps(deck.remaining), deck.last_quest_id),
            )

    def complete_quest(
        self, chat_id: int, week_key: str, event_id: int, title: str, now: datetime
    ) -> bool:
        """Complete an active quest and award its title in the same transaction."""
        with self._write():
            row = self.get_quest(chat_id, week_key)
            if row is None or row.status != "active":
                return False
            updated = self._conn.execute(
                "UPDATE quests SET status='completed', completed_event_id=? "
                "WHERE chat_id=? AND week_key=? AND status='active'",
                (event_id, chat_id, week_key),
            )
            if not updated.rowcount:
                return False
            self._conn.execute(
                "INSERT OR IGNORE INTO chat_titles (chat_id,quest_id,title,week_key,awarded_at) "
                "VALUES (?,?,?,?,?)",
                (chat_id, row.quest_id, title, week_key, _ts(now)),
            )
        return True

    def latest_chat_title(self, chat_id: int) -> str | None:
        row = self._conn.execute(
            "SELECT title FROM chat_titles WHERE chat_id=? ORDER BY id DESC LIMIT 1", (chat_id,)
        ).fetchone()
        return str(row[0]) if row else None

    def successful_hits_in_period(self, chat_id: int, start: datetime, end: datetime) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM hits WHERE chat_id=? AND created_at>=? "
                "AND created_at<? AND points>0",
                (chat_id, _ts(start), _ts(end)),
            ).fetchone()[0]
        )

    def max_hit_participants(self, chat_id: int, start: datetime, end: datetime) -> int:
        return int(
            self._conn.execute(
                "SELECT COALESCE(MAX(n),0) FROM (SELECT COUNT(DISTINCT user_id) n FROM hits "
                "WHERE chat_id=? AND created_at>=? AND created_at<? AND points>0 "
                "GROUP BY event_id)",
                (chat_id, _ts(start), _ts(end)),
            ).fetchone()[0]
        )

    def max_shoo_participants(self, chat_id: int, start: datetime, end: datetime) -> int:
        return int(
            self._conn.execute(
                "SELECT COALESCE(MAX(n),0) FROM (SELECT COUNT(DISTINCT user_id) n FROM shoos "
                "WHERE chat_id=? AND created_at>=? AND created_at<? GROUP BY event_id)",
                (chat_id, _ts(start), _ts(end)),
            ).fetchone()[0]
        )

    def max_hits_before_flee(self, chat_id: int, start: datetime, end: datetime) -> int:
        # Action order disambiguates hits and shoos with the same timestamp.
        return int(
            self._conn.execute(
                """SELECT COALESCE(MAX(n),0) FROM (
                SELECT COUNT(DISTINCT h.user_id) n FROM shoos s
                JOIN action_order sa ON sa.event_id=s.event_id AND sa.user_id=s.user_id
                    AND sa.kind='shoo'
                JOIN hits h ON h.event_id=s.event_id AND h.points>0
                JOIN action_order ha ON ha.event_id=h.event_id AND ha.user_id=h.user_id
                    AND ha.kind='hit' AND ha.id<sa.id
                WHERE s.chat_id=? AND s.created_at>=? AND s.created_at<? AND s.flew=1
                GROUP BY s.event_id
            )""",
                (chat_id, _ts(start), _ts(end)),
            ).fetchone()[0]
        )

    # weekly chronicle (ADR-0014)
    def get_chronicle(self, chat_id: int, week_key: str) -> Chronicle | None:
        row = self._conn.execute(
            "SELECT status, text FROM chronicles WHERE chat_id=? AND week_key=?",
            (chat_id, week_key),
        ).fetchone()
        return Chronicle(row["status"], row["text"]) if row else None

    def claim_chronicle(self, chat_id: int, week_key: str, status: str, now: datetime) -> bool:
        """Create the week's row. False if it already exists: one request per chat and week."""
        with self._write():
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO chronicles (chat_id, week_key, status, created_at) "
                "VALUES (?, ?, ?, ?)",
                (chat_id, week_key, status, _ts(now)),
            )
        return cur.rowcount == 1

    def finish_chronicle(self, chat_id: int, week_key: str, text: str | None) -> None:
        """Settle a pending chronicle: ready with its text, or failed without one."""
        with self._write():
            self._conn.execute(
                "UPDATE chronicles SET status=?, text=? "
                "WHERE chat_id=? AND week_key=? AND status='pending'",
                ("ready" if text else "failed", text, chat_id, week_key),
            )

    def chronicle_week(self, chat_id: int, start: datetime, end: datetime) -> ChronicleWeek:
        """Game metadata of arrivals opened in [start, end) of one chat. No message text exists."""
        window = (chat_id, _ts(start), _ts(end))
        types = {
            row["pigeon_type"]: int(row["n"])
            for row in self._conn.execute(
                "SELECT pigeon_type, COUNT(*) AS n FROM events "
                "WHERE chat_id=? AND opened_at>=? AND opened_at<? "
                "GROUP BY pigeon_type ORDER BY pigeon_type",
                window,
            )
        }
        scared = int(
            self._conn.execute(
                "SELECT COUNT(DISTINCT s.event_id) FROM shoos s JOIN events e "
                "ON e.id=s.event_id AND e.chat_id=s.chat_id "
                "WHERE s.chat_id=? AND e.opened_at>=? AND e.opened_at<? AND s.flew=1",
                window,
            ).fetchone()[0]
        )
        rows = self._conn.execute(
            """SELECT u.user_id, COALESCE(m.display_name, '?') AS name,
                COALESCE((SELECT SUM(l.delta) FROM points_ledger l WHERE l.chat_id=u.chat_id
                    AND l.user_id=u.user_id AND l.created_at>=? AND l.created_at<?), 0) AS points,
                COALESCE(SUM(u.hit), 0) AS hits, COALESCE(SUM(u.miss), 0) AS misses,
                COALESCE(SUM(u.big), 0) AS big_hits, COALESCE(SUM(u.shoo), 0) AS shoos,
                COALESCE(SUM(u.flew), 0) AS scared
            FROM (
                SELECT h.chat_id, h.user_id, (h.points > 0) AS hit, (h.outcome = 'miss') AS miss,
                    (h.outcome IN ('cap', 'chain')) AS big, 0 AS shoo, 0 AS flew
                FROM hits h JOIN events e ON e.id=h.event_id AND e.chat_id=h.chat_id
                WHERE h.chat_id=? AND e.opened_at>=? AND e.opened_at<?
                UNION ALL
                SELECT s.chat_id, s.user_id, 0, 0, 0, 1, s.flew
                FROM shoos s JOIN events e ON e.id=s.event_id AND e.chat_id=s.chat_id
                WHERE s.chat_id=? AND e.opened_at>=? AND e.opened_at<?
            ) u
            LEFT JOIN members m ON m.chat_id=u.chat_id AND m.user_id=u.user_id
            GROUP BY u.user_id
            ORDER BY points DESC, hits DESC, name ASC""",
            (_ts(start), _ts(end), *window, *window),
        ).fetchall()
        players = [
            ChroniclePlayer(
                row["name"],
                int(row["points"]),
                int(row["hits"]),
                int(row["misses"]),
                int(row["big_hits"]),
                int(row["shoos"]),
                int(row["scared"]),
            )
            for row in rows
        ]
        return ChronicleWeek(types, scared, players)
