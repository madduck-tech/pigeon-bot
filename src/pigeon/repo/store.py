"""SQLite storage. Every query is scoped by chat_id."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id              INTEGER PRIMARY KEY,
    enabled              INTEGER NOT NULL DEFAULT 0,
    timezone             TEXT NOT NULL DEFAULT 'UTC',
    quiet_start          INTEGER NOT NULL DEFAULT 23,
    quiet_end            INTEGER NOT NULL DEFAULT 9,
    dashboard_message_id INTEGER,
    dashboard_dirty      INTEGER NOT NULL DEFAULT 1,
    paused_until         TEXT,
    last_photo_poll_at   TEXT,
    created_at           TEXT NOT NULL
);
-- name is NULL until a vote replaces the starting name, which lives in render/.
CREATE TABLE IF NOT EXISTS pigeon (
    chat_id INTEGER PRIMARY KEY,
    name    TEXT,
    title   TEXT,
    born_at TEXT
);
CREATE TABLE IF NOT EXISTS members (
    chat_id      INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    username     TEXT,
    PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    source     TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'auto')),
    opened_at  TEXT NOT NULL,
    closes_at  TEXT NOT NULL,
    message_id INTEGER
);
CREATE INDEX IF NOT EXISTS events_chat_status ON events (chat_id, status);
CREATE TABLE IF NOT EXISTS hits (
    event_id   INTEGER NOT NULL REFERENCES events (id),
    chat_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (event_id, user_id)
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
    postpones  INTEGER NOT NULL DEFAULT 0,
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
CREATE TABLE IF NOT EXISTS dart_throws (
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    local_day   TEXT NOT NULL,
    event_id    INTEGER NOT NULL,
    dice_value  INTEGER NOT NULL,
    points      INTEGER NOT NULL,
    PRIMARY KEY (chat_id, user_id, local_day)
);
CREATE TABLE IF NOT EXISTS bets (
    event_id   INTEGER NOT NULL REFERENCES events (id),
    chat_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    choice     TEXT NOT NULL CHECK (choice IN ('ge3', 'lt3')),
    status     TEXT NOT NULL CHECK (status IN ('open', 'won', 'lost')),
    PRIMARY KEY (event_id, user_id)
);
-- Message counts per minute. No text is stored, only how many messages arrived.
CREATE TABLE IF NOT EXISTS message_minutes (
    chat_id INTEGER NOT NULL,
    minute  TEXT NOT NULL,
    count   INTEGER NOT NULL,
    PRIMARY KEY (chat_id, minute)
);
CREATE TABLE IF NOT EXISTS achievements (
    chat_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    code       TEXT NOT NULL,
    awarded_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, user_id, code)
);
CREATE TABLE IF NOT EXISTS quests (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id  INTEGER NOT NULL,
    week_key TEXT NOT NULL,
    target   INTEGER NOT NULL,
    status   TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'done', 'failed')),
    reported INTEGER NOT NULL DEFAULT 0,
    UNIQUE (chat_id, week_key)
);
CREATE TABLE IF NOT EXISTS fortunes (
    chat_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    local_day TEXT NOT NULL,
    PRIMARY KEY (chat_id, user_id, local_day)
);
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    run_at       TEXT NOT NULL,
    payload_json TEXT,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'done', 'failed')),
    attempts     INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_pending ON jobs (chat_id, kind)
    WHERE status = 'pending';
CREATE TABLE IF NOT EXISTS duels (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       INTEGER NOT NULL,
    challenger_id INTEGER NOT NULL,
    opponent_id   INTEGER NOT NULL,
    state         TEXT NOT NULL DEFAULT 'pending'
                  CHECK (state IN ('pending', 'active', 'done', 'declined', 'expired')),
    round         INTEGER NOT NULL DEFAULT 0,
    score_a       INTEGER NOT NULL DEFAULT 0,
    score_b       INTEGER NOT NULL DEFAULT 0,
    message_id    INTEGER,
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS duels_one_live ON duels (chat_id)
    WHERE state IN ('pending', 'active');
CREATE TABLE IF NOT EXISTS name_history (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    name    TEXT NOT NULL,
    from_at TEXT,
    to_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS name_votes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      INTEGER NOT NULL,
    options_json TEXT NOT NULL,
    message_id   INTEGER,
    status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    opened_at    TEXT NOT NULL,
    closes_at    TEXT NOT NULL,
    winner       TEXT,
    reported     INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS name_votes_one_open ON name_votes (chat_id)
    WHERE status = 'open';
"""

# Columns added after the first schema. Databases created earlier get them on startup.
MIGRATIONS = [
    ("chats", "timezone", "TEXT NOT NULL DEFAULT 'UTC'"),
    ("chats", "quiet_start", "INTEGER NOT NULL DEFAULT 23"),
    ("chats", "quiet_end", "INTEGER NOT NULL DEFAULT 9"),
    ("chats", "dashboard_message_id", "INTEGER"),
    ("chats", "dashboard_dirty", "INTEGER NOT NULL DEFAULT 1"),
    ("chats", "paused_until", "TEXT"),
    ("chats", "last_photo_poll_at", "TEXT"),
    ("members", "username", "TEXT"),
    ("events", "source", "TEXT NOT NULL DEFAULT 'manual'"),
    ("arrival_plans", "postpones", "INTEGER NOT NULL DEFAULT 0"),
]

# Rows for databases created before these tables existed. Safe to run on every start.
BACKFILL = """
INSERT OR IGNORE INTO pigeon (chat_id, born_at)
    SELECT chat_id, (SELECT MIN(opened_at) FROM events WHERE events.chat_id = chats.chat_id)
    FROM chats;
INSERT INTO trophy_log (chat_id, user_id, started_at)
    SELECT chat_id, holder_user_id, since FROM trophy
    WHERE chat_id NOT IN (SELECT chat_id FROM trophy_log);
"""


@dataclass(frozen=True)
class ChatSettings:
    chat_id: int
    timezone: str
    quiet_start: int
    quiet_end: int
    dashboard_message_id: int | None
    dashboard_dirty: bool
    paused_until: datetime | None = None


@dataclass(frozen=True)
class Pigeon:
    chat_id: int
    name: str | None
    title: str | None
    born_at: datetime | None


@dataclass(frozen=True)
class Quest:
    id: int
    chat_id: int
    week_key: str
    target: int
    status: str
    reported: bool = False


@dataclass(frozen=True)
class Job:
    id: int
    chat_id: int
    kind: str
    run_at: datetime
    payload: dict | None
    attempts: int


@dataclass(frozen=True)
class Duel:
    id: int
    chat_id: int
    challenger_id: int
    opponent_id: int
    state: str
    message_id: int | None
    expires_at: datetime


@dataclass(frozen=True)
class NameVote:
    id: int
    chat_id: int
    options: tuple[str, ...]
    message_id: int | None
    status: str
    opened_at: datetime
    closes_at: datetime
    winner: str | None
    reported: bool


@dataclass(frozen=True)
class Event:
    id: int
    chat_id: int
    status: str
    source: str
    opened_at: datetime
    closes_at: datetime
    message_id: int | None


@dataclass(frozen=True)
class ArrivalPlan:
    chat_id: int
    local_date: str
    planned_at: datetime | None
    status: str
    postpones: int = 0


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


def _event(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"],
        chat_id=row["chat_id"],
        status=row["status"],
        source=row["source"],
        opened_at=datetime.fromisoformat(row["opened_at"]),
        closes_at=datetime.fromisoformat(row["closes_at"]),
        message_id=row["message_id"],
    )


def _opt_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _quest(row: sqlite3.Row) -> Quest:
    return Quest(
        row["id"],
        row["chat_id"],
        row["week_key"],
        row["target"],
        row["status"],
        bool(row["reported"]),
    )


def _duel(row: sqlite3.Row) -> Duel:
    return Duel(
        id=row["id"],
        chat_id=row["chat_id"],
        challenger_id=row["challenger_id"],
        opponent_id=row["opponent_id"],
        state=row["state"],
        message_id=row["message_id"],
        expires_at=datetime.fromisoformat(row["expires_at"]),
    )


def _name_vote(row: sqlite3.Row) -> NameVote:
    return NameVote(
        id=row["id"],
        chat_id=row["chat_id"],
        options=tuple(json.loads(row["options_json"])),
        message_id=row["message_id"],
        status=row["status"],
        opened_at=datetime.fromisoformat(row["opened_at"]),
        closes_at=datetime.fromisoformat(row["closes_at"]),
        winner=row["winner"],
        reported=bool(row["reported"]),
    )


def _job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        chat_id=row["chat_id"],
        kind=row["kind"],
        run_at=datetime.fromisoformat(row["run_at"]),
        payload=json.loads(row["payload_json"]) if row["payload_json"] else None,
        attempts=row["attempts"],
    )


def _chat(row: sqlite3.Row) -> ChatSettings:
    return ChatSettings(
        chat_id=row["chat_id"],
        timezone=row["timezone"],
        quiet_start=row["quiet_start"],
        quiet_end=row["quiet_end"],
        dashboard_message_id=row["dashboard_message_id"],
        dashboard_dirty=bool(row["dashboard_dirty"]),
        paused_until=_opt_dt(row["paused_until"]),
    )


def minute_key(value: datetime) -> str:
    """UTC minute bucket used by the message counter, e.g. 2026-11-01T07:14:00+00:00."""
    return value.astimezone(UTC).replace(second=0, microsecond=0).isoformat()


class Store:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        for table, column, decl in MIGRATIONS:
            self._ensure_column(table, column, decl)
        self._conn.executescript(BACKFILL)

    def close(self) -> None:
        self._conn.close()

    def _ensure_column(self, table: str, column: str, decl: str) -> None:
        columns = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            with self._conn:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # chats
    def enable_chat(self, chat_id: int, now: datetime, timezone: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO chats (chat_id, enabled, timezone, created_at) VALUES (?, 1, ?, ?) "
                "ON CONFLICT (chat_id) DO UPDATE SET enabled = 1, timezone = excluded.timezone",
                (chat_id, timezone, _ts(now)),
            )
            self._conn.execute("INSERT OR IGNORE INTO pigeon (chat_id) VALUES (?)", (chat_id,))

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
                columns = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
                if "chat_id" in columns:
                    self._conn.execute(
                        f"UPDATE {table} SET chat_id = ? WHERE chat_id = ?",
                        (new_chat_id, old_chat_id),
                    )
            self._conn.execute(
                "UPDATE chats SET dashboard_dirty = 1 WHERE chat_id = ?", (new_chat_id,)
            )
        return True

    def set_paused_until(self, chat_id: int, until: datetime | None) -> None:
        value = _ts(until) if until else None
        with self._conn:
            self._conn.execute(
                "UPDATE chats SET paused_until = ? WHERE chat_id = ?", (value, chat_id)
            )

    # pigeon
    def get_pigeon(self, chat_id: int) -> Pigeon:
        row = self._conn.execute("SELECT * FROM pigeon WHERE chat_id = ?", (chat_id,)).fetchone()
        if row is None:
            return Pigeon(chat_id, None, None, None)
        return Pigeon(chat_id, row["name"], row["title"], _opt_dt(row["born_at"]))

    def set_quiet_hours(self, chat_id: int, start: int, end: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE chats SET quiet_start = ?, quiet_end = ? WHERE chat_id = ?",
                (start, end, chat_id),
            )
            self._conn.execute("UPDATE chats SET dashboard_dirty = 1 WHERE chat_id = ?", (chat_id,))

    def get_chat(self, chat_id: int) -> ChatSettings | None:
        row = self._conn.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        return _chat(row) if row else None

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

    # dashboard
    def set_dashboard_message(self, chat_id: int, message_id: int | None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE chats SET dashboard_message_id = ? WHERE chat_id = ?",
                (message_id, chat_id),
            )

    def mark_dashboard_dirty(self, chat_id: int) -> None:
        with self._conn:
            self._conn.execute("UPDATE chats SET dashboard_dirty = 1 WHERE chat_id = ?", (chat_id,))

    def clear_dashboard_dirty(self, chat_id: int) -> None:
        with self._conn:
            self._conn.execute("UPDATE chats SET dashboard_dirty = 0 WHERE chat_id = ?", (chat_id,))

    def dirty_dashboard_chats(self) -> list[int]:
        rows = self._conn.execute(
            "SELECT chat_id FROM chats WHERE enabled = 1 AND dashboard_dirty = 1"
        ).fetchall()
        return [row["chat_id"] for row in rows]

    # members
    def upsert_member(self, chat_id: int, user_id: int, display_name: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO members (chat_id, user_id, display_name) VALUES (?, ?, ?) "
                "ON CONFLICT (chat_id, user_id) DO UPDATE SET display_name = excluded.display_name",
                (chat_id, user_id, display_name),
            )

    def remember_member(
        self, chat_id: int, user_id: int, display_name: str, username: str | None
    ) -> None:
        """Update a member from a live Telegram user, whose username is the current one.

        A username can pass to someone else, so it is taken from any previous owner here.
        """
        with self._conn:
            if username is not None:
                self._conn.execute(
                    "UPDATE members SET username = NULL "
                    "WHERE chat_id = ? AND lower(username) = lower(?) AND user_id <> ?",
                    (chat_id, username, user_id),
                )
            self._conn.execute(
                "INSERT INTO members (chat_id, user_id, display_name, username) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (chat_id, user_id) DO UPDATE SET "
                "display_name = excluded.display_name, username = excluded.username",
                (chat_id, user_id, display_name, username),
            )

    def member_by_username(self, chat_id: int, username: str) -> int | None:
        row = self._conn.execute(
            "SELECT user_id FROM members WHERE chat_id = ? AND lower(username) = lower(?)",
            (chat_id, username),
        ).fetchone()
        return row["user_id"] if row else None

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
    def open_event(
        self, chat_id: int, now: datetime, closes_at: datetime, source: str = "manual"
    ) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO events (chat_id, status, source, opened_at, closes_at) "
                "VALUES (?, 'open', ?, ?, ?)",
                (chat_id, source, _ts(now), _ts(closes_at)),
            )
            # The first arrival is the pigeon's birthday.
            self._conn.execute(
                "UPDATE pigeon SET born_at = ? WHERE chat_id = ? AND born_at IS NULL",
                (_ts(now), chat_id),
            )
        return int(cur.lastrowid)

    def get_event(self, event_id: int) -> Event | None:
        row = self._conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return _event(row) if row else None

    def current_open_event(self, chat_id: int) -> Event | None:
        row = self._conn.execute(
            "SELECT * FROM events WHERE chat_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
        return _event(row) if row else None

    def delete_unsent_event(self, chat_id: int, event_id: int) -> bool:
        """Remove an arrival whose send failed, as if it never happened.

        Returns False and keeps it if anyone already acted on it: then the message did reach
        the chat and only Telegram's response was lost.
        """
        with self._conn:
            for table in ("hits", "bets", "dart_throws"):
                acted = self._conn.execute(
                    f"SELECT 1 FROM {table} WHERE event_id = ? AND chat_id = ? LIMIT 1",
                    (event_id, chat_id),
                ).fetchone()
                if acted:
                    return False
            self._conn.execute(
                "DELETE FROM events WHERE id = ? AND chat_id = ?", (event_id, chat_id)
            )
            self._conn.execute(
                "UPDATE pigeon SET born_at = "
                "(SELECT MIN(opened_at) FROM events WHERE events.chat_id = pigeon.chat_id) "
                "WHERE chat_id = ?",
                (chat_id,),
            )
        return True

    def close_event(self, event_id: int) -> None:
        with self._conn:
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
            "SELECT COUNT(*) AS n FROM hits WHERE event_id = ?", (event_id,)
        ).fetchone()
        return int(row["n"])

    # live-chat counter (counts only; no message text is kept)
    def record_message(self, chat_id: int, minute: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO message_minutes (chat_id, minute, count) VALUES (?, ?, 1) "
                "ON CONFLICT (chat_id, minute) DO UPDATE SET count = count + 1",
                (chat_id, minute),
            )

    def messages_since(self, chat_id: int, since_minute: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(count), 0) AS n FROM message_minutes "
            "WHERE chat_id = ? AND minute >= ?",
            (chat_id, since_minute),
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
        return ArrivalPlan(
            row["chat_id"], row["local_date"], planned, row["status"], row["postpones"]
        )

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

    def postpone_plan(self, chat_id: int, local_date: str, new_planned_at: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE arrival_plans SET planned_at = ?, postpones = postpones + 1 "
                "WHERE chat_id = ? AND local_date = ?",
                (_ts(new_planned_at), chat_id, local_date),
            )

    # hits and points
    def add_hit(
        self, chat_id: int, event_id: int, user_id: int, now: datetime, points: int
    ) -> bool:
        """Record a hit and its points atomically. Returns False if the user already hit."""
        with self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO hits (event_id, chat_id, user_id, created_at) "
                "VALUES (?, ?, ?, ?)",
                (event_id, chat_id, user_id, _ts(now)),
            )
            if cur.rowcount == 0:
                return False
            self._conn.execute(
                "INSERT INTO points_ledger (chat_id, user_id, delta, reason, ref_id, created_at) "
                "VALUES (?, ?, ?, 'hit', ?, ?)",
                (chat_id, user_id, points, event_id, _ts(now)),
            )
        return True

    def add_points(
        self, chat_id: int, user_id: int, delta: int, reason: str, ref_id: int | None, now: datetime
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO points_ledger (chat_id, user_id, delta, reason, ref_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, user_id, delta, reason, ref_id, _ts(now)),
            )

    # counts for achievements and the quest
    def hits_between(self, chat_id: int, start: datetime, end: datetime) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM hits WHERE chat_id = ? AND created_at >= ? "
            "AND created_at < ?",
            (chat_id, _ts(start), _ts(end)),
        ).fetchone()
        return int(row["n"])

    def hitters_between(self, chat_id: int, start: datetime, end: datetime) -> set[int]:
        rows = self._conn.execute(
            "SELECT DISTINCT user_id FROM hits WHERE chat_id = ? AND created_at >= ? "
            "AND created_at < ?",
            (chat_id, _ts(start), _ts(end)),
        ).fetchall()
        return {row["user_id"] for row in rows}

    def arrivals_between(self, chat_id: int, start: datetime, end: datetime) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE chat_id = ? AND opened_at >= ? "
            "AND opened_at < ?",
            (chat_id, _ts(start), _ts(end)),
        ).fetchone()
        return int(row["n"])

    def member_ids(self, chat_id: int) -> set[int]:
        rows = self._conn.execute(
            "SELECT user_id FROM members WHERE chat_id = ?", (chat_id,)
        ).fetchall()
        return {row["user_id"] for row in rows}

    def hits_in_last_arrivals(self, chat_id: int, user_id: int, count: int) -> int:
        """How many of the chat's last `count` arrivals this user hit."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM hits WHERE chat_id = ? AND user_id = ? AND event_id IN "
            "(SELECT id FROM events WHERE chat_id = ? ORDER BY id DESC LIMIT ?)",
            (chat_id, user_id, chat_id, count),
        ).fetchone()
        return int(row["n"])

    def first_hits(self, chat_id: int, user_id: int) -> int:
        """On how many arrivals this user was the first to hit."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM hits AS h WHERE h.chat_id = ? AND h.user_id = ? "
            "AND h.rowid = (SELECT MIN(rowid) FROM hits WHERE event_id = h.event_id)",
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

    # quests
    def ensure_quest(self, chat_id: int, week_key: str, target: int) -> Quest:
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO quests (chat_id, week_key, target) VALUES (?, ?, ?)",
                (chat_id, week_key, target),
            )
        quest = self.get_quest(chat_id, week_key)
        assert quest is not None
        return quest

    def get_quest(self, chat_id: int, week_key: str) -> Quest | None:
        row = self._conn.execute(
            "SELECT * FROM quests WHERE chat_id = ? AND week_key = ?", (chat_id, week_key)
        ).fetchone()
        return _quest(row) if row else None

    def complete_quest(self, chat_id: int, quest_id: int, title: str) -> None:
        """Finish the quest and give the pigeon its title together."""
        with self._conn:
            self._conn.execute(
                "UPDATE quests SET status = 'done' WHERE id = ? AND chat_id = ?",
                (quest_id, chat_id),
            )
            self._conn.execute("UPDATE pigeon SET title = ? WHERE chat_id = ?", (title, chat_id))

    def close_quests_through(self, chat_id: int, week_key: str) -> None:
        """Fail unfinished quests up to this week, and mark that week as reported."""
        with self._conn:
            self._conn.execute(
                "UPDATE quests SET status = 'failed' WHERE chat_id = ? AND status = 'active' "
                "AND week_key <= ?",
                (chat_id, week_key),
            )
            self._conn.execute(
                "UPDATE quests SET reported = 1 WHERE chat_id = ? AND week_key = ?",
                (chat_id, week_key),
            )

    # photo polls
    def claim_photo_poll(self, chat_id: int, now: datetime, not_after: datetime) -> bool:
        """Take the chat's photo poll slot. False if one was sent after `not_after`."""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE chats SET last_photo_poll_at = ? WHERE chat_id = ? "
                "AND (last_photo_poll_at IS NULL OR last_photo_poll_at <= ?)",
                (_ts(now), chat_id, _ts(not_after)),
            )
        return cur.rowcount > 0

    # duels
    def create_duel(
        self,
        chat_id: int,
        challenger_id: int,
        opponent_id: int,
        now: datetime,
        expires_at: datetime,
    ) -> int | None:
        """Open a challenge. None if the chat already has a live duel."""
        with self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO duels "
                "(chat_id, challenger_id, opponent_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (chat_id, challenger_id, opponent_id, _ts(now), _ts(expires_at)),
            )
        return int(cur.lastrowid) if cur.rowcount else None

    def get_duel(self, chat_id: int, duel_id: int) -> Duel | None:
        row = self._conn.execute(
            "SELECT * FROM duels WHERE id = ? AND chat_id = ?", (duel_id, chat_id)
        ).fetchone()
        return _duel(row) if row else None

    def set_duel_message(self, chat_id: int, duel_id: int, message_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE duels SET message_id = ? WHERE id = ? AND chat_id = ?",
                (message_id, duel_id, chat_id),
            )

    def delete_duel(self, chat_id: int, duel_id: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM duels WHERE id = ? AND chat_id = ?", (duel_id, chat_id))

    def activate_duel(self, chat_id: int, duel_id: int, expires_at: datetime) -> bool:
        """Start the bout. False if the challenge was already answered or swept."""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE duels SET state = 'active', expires_at = ? "
                "WHERE id = ? AND chat_id = ? AND state = 'pending'",
                (_ts(expires_at), duel_id, chat_id),
            )
        return cur.rowcount > 0

    def decline_duel(self, chat_id: int, duel_id: int) -> bool:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE duels SET state = 'declined' "
                "WHERE id = ? AND chat_id = ? AND state = 'pending'",
                (duel_id, chat_id),
            )
        return cur.rowcount > 0

    def expire_duel(self, chat_id: int, duel_id: int) -> bool:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE duels SET state = 'expired' "
                "WHERE id = ? AND chat_id = ? AND state IN ('pending', 'active')",
                (duel_id, chat_id),
            )
        return cur.rowcount > 0

    def finish_duel(
        self,
        chat_id: int,
        duel_id: int,
        rounds: int,
        score_a: int,
        score_b: int,
        winner_id: int | None,
        points: int,
        now: datetime,
    ) -> bool:
        """Record the result and the prize together. False if the bout was already swept."""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE duels SET state = 'done', round = ?, score_a = ?, score_b = ? "
                "WHERE id = ? AND chat_id = ? AND state = 'active'",
                (rounds, score_a, score_b, duel_id, chat_id),
            )
            if cur.rowcount == 0:
                return False
            if winner_id is not None:
                self._conn.execute(
                    "INSERT INTO points_ledger "
                    "(chat_id, user_id, delta, reason, ref_id, created_at) "
                    "VALUES (?, ?, ?, 'duel_win', ?, ?)",
                    (chat_id, winner_id, points, duel_id, _ts(now)),
                )
        return True

    def stale_duels(self, now: datetime) -> list[Duel]:
        rows = self._conn.execute(
            "SELECT d.* FROM duels AS d JOIN chats AS c ON c.chat_id = d.chat_id "
            "WHERE c.enabled = 1 AND d.state IN ('pending', 'active') AND d.expires_at <= ?",
            (_ts(now),),
        ).fetchall()
        return [_duel(row) for row in rows]

    # name votes
    def create_name_vote(
        self, chat_id: int, options: list[str], now: datetime, closes_at: datetime
    ) -> int | None:
        """Open a vote. None if the chat already has one open."""
        with self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO name_votes (chat_id, options_json, opened_at, closes_at) "
                "VALUES (?, ?, ?, ?)",
                (chat_id, json.dumps(options, ensure_ascii=False), _ts(now), _ts(closes_at)),
            )
        return int(cur.lastrowid) if cur.rowcount else None

    def set_name_vote_message(self, chat_id: int, vote_id: int, message_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE name_votes SET message_id = ? WHERE id = ? AND chat_id = ?",
                (message_id, vote_id, chat_id),
            )

    def delete_name_vote(self, chat_id: int, vote_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM name_votes WHERE id = ? AND chat_id = ?", (vote_id, chat_id)
            )

    def open_name_vote(self, chat_id: int) -> NameVote | None:
        row = self._conn.execute(
            "SELECT * FROM name_votes WHERE chat_id = ? AND status = 'open'", (chat_id,)
        ).fetchone()
        return _name_vote(row) if row else None

    def unreported_name_vote(self, chat_id: int) -> NameVote | None:
        row = self._conn.execute(
            "SELECT * FROM name_votes WHERE chat_id = ? AND status = 'closed' AND reported = 0 "
            "ORDER BY id DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
        return _name_vote(row) if row else None

    def last_name_vote_at(self, chat_id: int) -> datetime | None:
        row = self._conn.execute(
            "SELECT MAX(opened_at) AS at FROM name_votes WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return _opt_dt(row["at"])

    def close_name_vote(
        self,
        chat_id: int,
        vote_id: int,
        winner: str | None,
        old_name: str,
        now: datetime,
        reported: bool = False,
    ) -> None:
        """Close the vote, and with a winner rename the pigeon and log the old name together."""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE name_votes SET status = 'closed', winner = ?, reported = ? "
                "WHERE id = ? AND chat_id = ? AND status = 'open'",
                (winner, int(reported), vote_id, chat_id),
            )
            if cur.rowcount == 0 or winner is None:
                return
            row = self._conn.execute(
                "SELECT COALESCE((SELECT MAX(to_at) FROM name_history WHERE chat_id = ?), "
                "(SELECT born_at FROM pigeon WHERE chat_id = ?)) AS from_at",
                (chat_id, chat_id),
            ).fetchone()
            self._conn.execute(
                "INSERT INTO name_history (chat_id, name, from_at, to_at) VALUES (?, ?, ?, ?)",
                (chat_id, old_name, row["from_at"], _ts(now)),
            )
            self._conn.execute("UPDATE pigeon SET name = ? WHERE chat_id = ?", (winner, chat_id))

    def mark_name_vote_reported(self, chat_id: int, vote_id: int) -> None:
        """This vote is settled, and so are older ones: their results are out of date."""
        with self._conn:
            self._conn.execute(
                "UPDATE name_votes SET reported = 1 WHERE id <= ? AND chat_id = ?",
                (vote_id, chat_id),
            )

    def past_names(self, chat_id: int) -> set[str]:
        rows = self._conn.execute(
            "SELECT name FROM name_history WHERE chat_id = ?", (chat_id,)
        ).fetchall()
        return {row["name"] for row in rows}

    # fortunes
    def claim_fortune(self, chat_id: int, user_id: int, local_day: str) -> bool:
        """Take today's prediction. False if the user already had one."""
        with self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO fortunes (chat_id, user_id, local_day) VALUES (?, ?, ?)",
                (chat_id, user_id, local_day),
            )
        return cur.rowcount > 0

    # jobs
    def ensure_job(
        self, chat_id: int, kind: str, run_at: datetime, payload: dict | None = None
    ) -> None:
        """Queue a job unless one of this kind is already pending for the chat."""
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO jobs (chat_id, kind, run_at, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (
                    chat_id,
                    kind,
                    _ts(run_at),
                    json.dumps(payload) if payload is not None else None,
                ),
            )

    def due_jobs(self, now: datetime) -> list[Job]:
        rows = self._conn.execute(
            "SELECT j.* FROM jobs AS j JOIN chats AS c ON c.chat_id = j.chat_id "
            "WHERE j.status = 'pending' AND c.enabled = 1 AND j.run_at <= ? ORDER BY j.run_at",
            (_ts(now),),
        ).fetchall()
        return [_job(row) for row in rows]

    def pending_job(self, chat_id: int, kind: str) -> Job | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE chat_id = ? AND kind = ? AND status = 'pending'",
            (chat_id, kind),
        ).fetchone()
        return _job(row) if row else None

    def finish_job(self, job_id: int) -> None:
        self._set_job_status(job_id, "done")

    def fail_job(self, job_id: int) -> None:
        self._set_job_status(job_id, "failed")

    def _set_job_status(self, job_id: int, status: str) -> None:
        with self._conn:
            self._conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))

    def reschedule_job(self, job_id: int, run_at: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE jobs SET run_at = ? WHERE id = ?",
                (_ts(run_at), job_id),
            )

    def retry_job(self, job_id: int, run_at: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE jobs SET run_at = ?, attempts = attempts + 1 WHERE id = ?",
                (_ts(run_at), job_id),
            )

    def balance(self, chat_id: int, user_id: int) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(delta), 0) AS n FROM points_ledger "
            "WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ).fetchone()
        return int(row["n"])

    # darts: one throw per user per local day
    def record_dart(
        self,
        chat_id: int,
        user_id: int,
        local_day: str,
        event_id: int,
        dice_value: int,
        points: int,
        now: datetime,
    ) -> bool:
        """Store the throw and its points together. False if the user already threw today."""
        with self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO dart_throws "
                "(chat_id, user_id, local_day, event_id, dice_value, points) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, user_id, local_day, event_id, dice_value, points),
            )
            if cur.rowcount == 0:
                return False
            if points:
                self._conn.execute(
                    "INSERT INTO points_ledger "
                    "(chat_id, user_id, delta, reason, ref_id, created_at) "
                    "VALUES (?, ?, ?, 'dart', ?, ?)",
                    (chat_id, user_id, points, event_id, _ts(now)),
                )
        return True

    def has_thrown_today(self, chat_id: int, user_id: int, local_day: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM dart_throws WHERE chat_id = ? AND user_id = ? AND local_day = ?",
            (chat_id, user_id, local_day),
        ).fetchone()
        return row is not None

    # bets: a fixed stake on one event, settled when the event closes
    def place_bet(
        self, chat_id: int, event_id: int, user_id: int, choice: str, stake: int, now: datetime
    ) -> bool:
        """Record the bet and take the stake together. False if the user already bet here."""
        with self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO bets (event_id, chat_id, user_id, choice, status) "
                "VALUES (?, ?, ?, ?, 'open')",
                (event_id, chat_id, user_id, choice),
            )
            if cur.rowcount == 0:
                return False
            self._conn.execute(
                "INSERT INTO points_ledger (chat_id, user_id, delta, reason, ref_id, created_at) "
                "VALUES (?, ?, ?, 'bet_stake', ?, ?)",
                (chat_id, user_id, -stake, event_id, _ts(now)),
            )
        return True

    def open_bets(self, event_id: int) -> list[tuple[int, str]]:
        rows = self._conn.execute(
            "SELECT user_id, choice FROM bets WHERE event_id = ? AND status = 'open'",
            (event_id,),
        ).fetchall()
        return [(row["user_id"], row["choice"]) for row in rows]

    def set_bet_status(self, event_id: int, user_id: int, status: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE bets SET status = ? WHERE event_id = ? AND user_id = ?",
                (status, event_id, user_id),
            )

    def leaderboard(
        self, chat_id: int, limit: int = 10, since: datetime | None = None
    ) -> list[tuple[str, int]]:
        sql = (
            "SELECT COALESCE(m.display_name, '?') AS name, SUM(l.delta) AS total "
            "FROM points_ledger AS l "
            "LEFT JOIN members AS m ON m.chat_id = l.chat_id AND m.user_id = l.user_id "
            "WHERE l.chat_id = ? "
        )
        params: list = [chat_id]
        if since is not None:
            sql += "AND l.created_at >= ? "
            params.append(_ts(since))
        sql += "GROUP BY l.user_id ORDER BY total DESC, name ASC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [(row["name"], int(row["total"])) for row in rows]
