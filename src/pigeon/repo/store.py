"""SQLite storage. Every query is scoped by chat_id."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
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
    created_at           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    chat_id      INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    display_name TEXT NOT NULL,
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
-- Message counts per minute. No text is stored, only how many messages arrived.
CREATE TABLE IF NOT EXISTS message_minutes (
    chat_id INTEGER NOT NULL,
    minute  TEXT NOT NULL,
    count   INTEGER NOT NULL,
    PRIMARY KEY (chat_id, minute)
);
"""

# Columns added after the first schema. Databases created earlier get them on startup.
MIGRATIONS = [
    ("chats", "timezone", "TEXT NOT NULL DEFAULT 'UTC'"),
    ("chats", "quiet_start", "INTEGER NOT NULL DEFAULT 23"),
    ("chats", "quiet_end", "INTEGER NOT NULL DEFAULT 9"),
    ("chats", "dashboard_message_id", "INTEGER"),
    ("chats", "dashboard_dirty", "INTEGER NOT NULL DEFAULT 1"),
    ("events", "source", "TEXT NOT NULL DEFAULT 'manual'"),
    ("arrival_plans", "postpones", "INTEGER NOT NULL DEFAULT 0"),
]


@dataclass(frozen=True)
class ChatSettings:
    chat_id: int
    timezone: str
    quiet_start: int
    quiet_end: int
    dashboard_message_id: int | None
    dashboard_dirty: bool


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
    return value.isoformat()


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


def _chat(row: sqlite3.Row) -> ChatSettings:
    return ChatSettings(
        chat_id=row["chat_id"],
        timezone=row["timezone"],
        quiet_start=row["quiet_start"],
        quiet_end=row["quiet_end"],
        dashboard_message_id=row["dashboard_message_id"],
        dashboard_dirty=bool(row["dashboard_dirty"]),
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
    def set_dashboard_message(self, chat_id: int, message_id: int) -> None:
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
                "INSERT INTO trophy (chat_id, holder_user_id, holder_name, since) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (chat_id) DO UPDATE SET "
                "holder_user_id = excluded.holder_user_id, holder_name = excluded.holder_name, "
                "since = excluded.since",
                (chat_id, user_id, name, _ts(now)),
            )
        return True

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
            params.append(_ts(since.astimezone(UTC)))
        sql += "GROUP BY l.user_id ORDER BY total DESC, name ASC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [(row["name"], int(row["total"])) for row in rows]
