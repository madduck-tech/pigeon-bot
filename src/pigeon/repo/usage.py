"""Bounded usage metadata and read-only reporting over the existing game facts."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

COMMANDS = (
    "pigeon",
    "help",
    "stats",
    "profile",
    "fortune",
    "setup",
    "quiet",
    "pause",
    "arrive",
    "adult",
)
METRICS = tuple(f"command.{name}" for name in COMMANDS) + (
    "membership.added",
    "membership.removed",
    "setup.saved",
    "delivery.message_error",
    "delivery.callback_error",
)
RETENTION_DAYS = 90
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS usage_counters (
    chat_id INTEGER NOT NULL, utc_day TEXT NOT NULL,
    metric TEXT NOT NULL CHECK (metric IN ({",".join(repr(m) for m in METRICS)})),
    count INTEGER NOT NULL CHECK (count > 0), PRIMARY KEY (chat_id, utc_day, metric)
);
CREATE TABLE IF NOT EXISTS usage_actors (
    chat_id INTEGER NOT NULL, utc_day TEXT NOT NULL, user_id INTEGER NOT NULL,
    PRIMARY KEY (chat_id, utc_day, user_id)
);
CREATE TABLE IF NOT EXISTS usage_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1), started_at TEXT NOT NULL
);
"""


class UsageRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def start(self, now: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO usage_meta VALUES (1, ?)",
                (now.astimezone(UTC).isoformat(),),
            )

    def record(
        self, chat_id: int, now: datetime, metric: str | None, user_id: int | None = None
    ) -> None:
        if metric is not None and metric not in METRICS:
            raise ValueError("unsupported usage metric")
        day = now.astimezone(UTC).date().isoformat()
        with self.conn:
            if metric is not None:
                self.conn.execute(
                    "INSERT INTO usage_counters VALUES (?, ?, ?, 1) "
                    "ON CONFLICT(chat_id, utc_day, metric) DO UPDATE SET count=count+1",
                    (chat_id, day, metric),
                )
            if user_id is not None:
                self.conn.execute(
                    "INSERT OR IGNORE INTO usage_actors VALUES (?, ?, ?)",
                    (chat_id, day, user_id),
                )

    def cleanup(self, now: datetime) -> None:
        cutoff = (now.astimezone(UTC).date() - timedelta(days=RETENTION_DAYS - 1)).isoformat()
        with self.conn:
            for table in ("usage_counters", "usage_actors"):
                self.conn.execute(f"DELETE FROM {table} WHERE utc_day < ?", (cutoff,))

    def has_chat(self, chat_id: int) -> bool:
        return any(
            self.conn.execute(
                f"SELECT 1 FROM {table} WHERE chat_id=? LIMIT 1", (chat_id,)
            ).fetchone()
            is not None
            for table in ("usage_counters", "usage_actors")
        )


def read_report(path: Path, now: datetime, days: int = 7, chat_id: int | None = None) -> dict:
    """Open an existing database without Store, migration, creation or write privileges."""
    if not 1 <= days <= RETENTION_DAYS:
        raise ValueError("days must be between 1 and 90")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")  # One consistent snapshot across every aggregate.
        return _report(conn, now, days, chat_id)
    finally:
        conn.close()


def _report(conn: sqlite3.Connection, now: datetime, days: int, chat_id: int | None) -> dict:
    today = now.astimezone(UTC).date()
    start = (today - timedelta(days=days - 1)).isoformat()
    end = (today + timedelta(days=1)).isoformat()
    scope = " AND chat_id=?" if chat_id is not None else ""
    ids = (chat_id,) if chat_id is not None else ()
    params = (start, end, *ids)
    rows: dict[tuple[int, str], dict[str, int]] = defaultdict(dict)

    def collect(sql: str, prefix: str = "") -> None:
        for row in conn.execute(sql, params):
            rows[row["chat_id"], row["day"]][prefix + row["metric"]] = row["n"]

    started = conn.execute("SELECT started_at FROM usage_meta WHERE id=1").fetchone()
    collect(
        "SELECT chat_id, utc_day AS day, metric, count AS n FROM usage_counters "
        "WHERE utc_day>=? AND utc_day<?" + scope
    )
    collect(
        "SELECT chat_id, utc_day AS day, 'participants' AS metric, COUNT(*) AS n "
        "FROM usage_actors WHERE utc_day>=? AND utc_day<?" + scope + " GROUP BY chat_id, utc_day"
    )
    collect(
        "SELECT chat_id, date(opened_at) AS day, 'arrival.' || source || '.' || pigeon_type "
        "AS metric, COUNT(*) AS n FROM events WHERE date(opened_at)>=? AND date(opened_at)<?"
        + scope
        + " GROUP BY chat_id, day, source, pigeon_type"
    )
    # Closed outcomes belong to the arrival's opening day; open is not 'flew alone'.
    collect(
        "SELECT e.chat_id, date(e.opened_at) AS day, 'arrival.' || CASE "
        "WHEN e.status='open' THEN 'open' WHEN EXISTS (SELECT 1 FROM shoos s "
        "WHERE s.chat_id=e.chat_id AND s.event_id=e.id AND s.flew=1) THEN 'scared' "
        "ELSE 'alone' END AS metric, COUNT(*) AS n FROM events e "
        "WHERE date(opened_at)>=? AND date(opened_at)<?"
        + (" AND e.chat_id=?" if chat_id is not None else "")
        + " GROUP BY e.chat_id, day, metric"
    )
    collect(
        "SELECT chat_id, date(created_at) AS day, 'hit.' || outcome AS metric, COUNT(*) AS n "
        "FROM hits WHERE date(created_at)>=? AND date(created_at)<?"
        + scope
        + " GROUP BY chat_id, day, outcome"
    )
    collect(
        "SELECT chat_id, date(created_at) AS day, 'shoo.attempts' AS metric, COUNT(*) AS n "
        "FROM shoos WHERE date(created_at)>=? AND date(created_at)<?"
        + scope
        + " GROUP BY chat_id, day"
    )
    collect(
        "SELECT chat_id, date(created_at) AS day, 'chronicle.' || status AS metric, COUNT(*) AS n "
        "FROM chronicles WHERE date(created_at)>=? AND date(created_at)<?"
        + scope
        + " GROUP BY chat_id, day, status"
    )
    local_fortunes = []
    for chat in conn.execute("SELECT chat_id, timezone FROM chats WHERE 1=1" + scope, ids):
        local_today = now.astimezone(ZoneInfo(chat["timezone"])).date()
        local_start = (local_today - timedelta(days=days - 1)).isoformat()
        local_end = (local_today + timedelta(days=1)).isoformat()
        local_fortunes.extend(
            dict(r)
            for r in conn.execute(
                "SELECT chat_id, local_day, COUNT(*) AS count FROM fortunes "
                "WHERE chat_id=? AND local_day>=? AND local_day<? GROUP BY chat_id, local_day",
                (chat["chat_id"], local_start, local_end),
            )
        )
    participants = {
        r["chat_id"]: r["n"]
        for r in conn.execute(
            "SELECT chat_id, COUNT(DISTINCT user_id) AS n FROM usage_actors "
            "WHERE utc_day>=? AND utc_day<?" + scope + " GROUP BY chat_id",
            params,
        )
    }
    week = (today - timedelta(days=6)).isoformat()
    active = conn.execute(
        "SELECT COUNT(DISTINCT chat_id) FROM usage_actors WHERE utc_day>=? AND utc_day<?" + scope,
        (week, end, *ids),
    ).fetchone()[0]
    enabled = conn.execute("SELECT COUNT(*) FROM chats WHERE enabled=1" + scope, ids).fetchone()[0]
    return {
        "start": start,
        "end": today.isoformat(),
        "started_at": started[0] if started else None,
        "enabled_chats": enabled,
        "active_chats_7d": active,
        "participants": participants,
        "days": [{"chat_id": c, "utc_day": d, "metrics": m} for (c, d), m in sorted(rows.items())],
        "local_fortunes": local_fortunes,
        "local_days": days,
    }
