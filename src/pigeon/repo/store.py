"""SQLite storage. Every query is scoped by chat_id."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id    INTEGER PRIMARY KEY,
    enabled    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
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
"""


@dataclass(frozen=True)
class Event:
    id: int
    chat_id: int
    status: str
    opened_at: datetime
    closes_at: datetime
    message_id: int | None


def _ts(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.isoformat()


def _event(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"],
        chat_id=row["chat_id"],
        status=row["status"],
        opened_at=datetime.fromisoformat(row["opened_at"]),
        closes_at=datetime.fromisoformat(row["closes_at"]),
        message_id=row["message_id"],
    )


class Store:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # chats
    def enable_chat(self, chat_id: int, now: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO chats (chat_id, enabled, created_at) VALUES (?, 1, ?) "
                "ON CONFLICT (chat_id) DO UPDATE SET enabled = 1",
                (chat_id, _ts(now)),
            )

    def is_enabled(self, chat_id: int) -> bool:
        row = self._conn.execute(
            "SELECT enabled FROM chats WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return bool(row and row["enabled"])

    # members
    def upsert_member(self, chat_id: int, user_id: int, display_name: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO members (chat_id, user_id, display_name) VALUES (?, ?, ?) "
                "ON CONFLICT (chat_id, user_id) DO UPDATE SET display_name = excluded.display_name",
                (chat_id, user_id, display_name),
            )

    # events
    def open_event(self, chat_id: int, now: datetime, closes_at: datetime) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO events (chat_id, status, opened_at, closes_at) "
                "VALUES (?, 'open', ?, ?)",
                (chat_id, _ts(now), _ts(closes_at)),
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

    def hit_count(self, event_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM hits WHERE event_id = ?", (event_id,)
        ).fetchone()
        return int(row["n"])

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

    def leaderboard(self, chat_id: int, limit: int = 10) -> list[tuple[str, int]]:
        rows = self._conn.execute(
            "SELECT COALESCE(m.display_name, '?') AS name, SUM(l.delta) AS total "
            "FROM points_ledger AS l "
            "LEFT JOIN members AS m ON m.chat_id = l.chat_id AND m.user_id = l.user_id "
            "WHERE l.chat_id = ? "
            "GROUP BY l.user_id ORDER BY total DESC, name ASC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [(row["name"], int(row["total"])) for row in rows]
