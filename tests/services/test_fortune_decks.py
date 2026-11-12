import random
from collections import Counter
from datetime import date, timedelta

import pytest

from pigeon.content import Fortune, FortuneFormat, _fortunes
from pigeon.repo.store import Store
from pigeon.services.fortune_decks import prepare
from rigged import CONTENT


def test_content_distribution_and_rejected_chain_fields():
    assert Counter(f.format for f in CONTENT.fortunes) == {
        "classic": 30,
        "omen": 8,
        "scene": 6,
        "spread": 6,
        "chat": 6,
    }
    with pytest.raises(ValueError):
        _fortunes([{"id": "x", "format": "scene", "text": "{name.__class__}"}])
    with pytest.raises(ValueError):
        _fortunes([{"id": "x", "format": "serial", "text": "x", "part": 1, "next": "x"}])


def test_standalone_deck_survives_restart(tmp_path):
    path = tmp_path / "db"
    store = Store(path)
    rng = random.Random(12)
    day = date(2026, 1, 1)
    for i in range(56):
        local_day = (day + timedelta(days=i)).isoformat()
        assert prepare(store, -1, 0, local_day, CONTENT.fortunes, CONTENT.names, rng)
        if i == 20:
            store.close()
            store = Store(path)
    rows = store._conn.execute(
        "SELECT fortune_id FROM daily_fortunes ORDER BY local_day"
    ).fetchall()
    ids = [row[0] for row in rows]
    assert len(set(ids)) == 56
    store.close()


def test_personal_history_and_content_updates(tmp_path):
    store = Store(tmp_path / "db")
    rng = random.Random(2)
    day = date(2026, 1, 1)
    for i in range(20):
        date_str = (day + timedelta(days=i)).isoformat()
        prepare(store, -1, i % 2 + 1, date_str, CONTENT.fortunes, CONTENT.names, rng)
    ids = [r[0] for r in store._conn.execute("SELECT fortune_id FROM fortunes ORDER BY local_day")]
    for i, ident in enumerate(ids):
        assert ident not in ids[max(0, i - 13) : i]
    old = CONTENT.fortunes
    new = old[1:] + (Fortune("new-one", FortuneFormat.CLASSIC, "new"),)
    prepare(store, -1, 1, "2026-02-01", new, CONTENT.names, rng)
    row = store._conn.execute(
        "SELECT remaining_json, known_json FROM fortune_decks WHERE owner_id=1"
    ).fetchone()
    assert "seeds-fall" not in row["remaining_json"]
    assert "new-one" in row["known_json"]
    store.close()


def test_retry_keeps_exact_rendered_text(tmp_path):
    path = tmp_path / "db"
    store = Store(path)
    scene = [Fortune("scene", FortuneFormat.SCENE, "{name} < &")]
    first = prepare(store, -1, 0, "2026-01-01", scene, ["<Гена>"], random.Random(1))
    store.close()
    store = Store(path)
    retry = prepare(store, -1, 0, "2026-01-01", scene, ["other"], random.Random(3))
    assert first == retry and "&lt;Гена&gt;" in retry and "&amp;" in retry
    store.close()


def test_existing_daily_rows_are_marked_sent_only_once(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE daily_fortunes (chat_id INTEGER, local_day TEXT,
                                     PRIMARY KEY (chat_id, local_day));
        INSERT INTO daily_fortunes VALUES (-1, '2026-01-01');
        PRAGMA user_version = 1;
    """)
    conn.close()
    store = Store(path)
    row = store._conn.execute("SELECT * FROM daily_fortunes").fetchone()
    assert row["sent_at"] is not None and row["fortune_id"] is None
    prepare(store, -1, 0, "2026-01-02", CONTENT.fortunes, CONTENT.names, random.Random(1))
    store.close()
    store = Store(path)
    row = store._conn.execute(
        "SELECT sent_at FROM daily_fortunes WHERE local_day='2026-01-02'"
    ).fetchone()
    assert row["sent_at"] is None
    store.close()


def test_failed_issue_write_rolls_back_deck(tmp_path, monkeypatch):
    store = Store(tmp_path / "db")

    def fail(*args):
        raise RuntimeError("write failed")

    monkeypatch.setattr(store, "save_fortune_issue", fail)
    with pytest.raises(RuntimeError, match="write failed"):
        prepare(store, -1, 0, "2026-01-01", CONTENT.fortunes, CONTENT.names, random.Random(1))
    assert store.get_fortune_deck(-1, "daily", 0) is None
    assert store.get_fortune_issue(-1, 0, "2026-01-01") is None
    store.close()
