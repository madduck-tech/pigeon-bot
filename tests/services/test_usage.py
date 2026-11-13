"""Usage contract: real SQLite, game services, retention, migration and read-only CLI."""

import random
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone

import pytest

from pigeon.clock import FixedClock
from pigeon.repo.store import Store
from pigeon.repo.usage import read_report
from pigeon.services import fortune, game
from pigeon.services.usage import Usage
from rigged import CONTENT
from tools.stats import render

NOW = datetime(2026, 11, 13, 23, 30, tzinfo=UTC)
CHAT, OTHER, NEW = -1001, -1002, -1003


@pytest.fixture
def env(tmp_path):
    path = tmp_path / "usage.db"
    store = Store(path)
    clock = FixedClock(NOW)
    usage = Usage(store.usage, clock)
    yield store, clock, usage, path
    store.close()


def metrics(report, chat=CHAT):
    return {r["utc_day"]: r["metrics"] for r in report["days"] if r["chat_id"] == chat}


def test_counts_uniqueness_period_union_and_chat_isolation(env):
    store, clock, usage, path = env
    for _ in range(3):
        usage.record(CHAT, "command.help", 1)
    usage.record(CHAT, user_id=2)
    usage.record(OTHER, "command.help", 1)
    clock.set(NOW + timedelta(days=1))
    usage.record(CHAT, "command.stats", 1)
    report = read_report(path, clock.now())
    assert metrics(report)["2026-11-13"] == {"command.help": 3, "participants": 2}
    assert metrics(report)["2026-11-14"] == {"command.stats": 1, "participants": 1}
    assert report["participants"] == {CHAT: 2, OTHER: 1}
    assert report["active_chats_7d"] == 2
    scoped = read_report(path, clock.now(), chat_id=CHAT)
    assert scoped["participants"] == {CHAT: 2}
    assert scoped["active_chats_7d"] == 1


def test_cleanup_cutoff_restart_and_read_filter_when_cleanup_does_not_run(env):
    store, clock, usage, path = env
    store.enable_chat(CHAT, NOW, "UTC")
    event = game.arrive(store, clock, CHAT)
    usage.record(CHAT, "command.help", 1)
    clock.set(NOW + timedelta(days=89))
    usage.record(CHAT, "command.help", 2)
    usage.maintain()
    assert len(store._conn.execute("SELECT * FROM usage_actors").fetchall()) == 2
    clock.set(NOW + timedelta(days=90))
    # Old rows still physically exist, but never leak into a report.
    report = read_report(path, clock.now(), days=90)
    assert report["participants"] == {CHAT: 1}
    assert list(metrics(report)) == [(NOW + timedelta(days=89)).date().isoformat()]
    Usage(store.usage, clock)  # Restart cleans up without an incoming action.
    assert store._conn.execute("SELECT COUNT(*) FROM usage_actors").fetchone()[0] == 1
    assert store.get_event(event.id) is not None
    assert read_report(path, clock.now())["started_at"] == NOW.isoformat()


def test_utc_boundary_and_global_activity_window(env):
    store, clock, usage, path = env
    store.enable_chat(CHAT, NOW, "Pacific/Kiritimati")
    store.enable_chat(OTHER, NOW, "UTC")
    clock.set(NOW - timedelta(days=7))
    usage.record(OTHER, "command.help", 1)
    clock.set(NOW - timedelta(days=6))
    usage.record(CHAT, "command.help", 2)
    clock.set(NOW.astimezone(timezone(timedelta(hours=3))))
    usage.record(CHAT, "command.stats", 2)
    store.disable_chat(CHAT)
    report = read_report(path, clock.now())
    assert report["enabled_chats"] == 1
    assert report["active_chats_7d"] == 1  # Includes disabled, excludes day -7.
    assert "2026-11-13" in metrics(report)
    clock.set(NOW + timedelta(minutes=30))
    usage.record(CHAT, "command.help", 2)
    assert "2026-11-14" in metrics(read_report(path, clock.now(), days=1))


def test_derived_metrics_do_not_duplicate_game_facts(env):
    store, clock, usage, path = env
    store.enable_chat(CHAT, NOW, "Europe/Moscow")
    first = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, first.id, 1, "SECRET-NAME")
    # A timed-out event and an open one must be distinguished.
    clock.set(first.closes_at)
    game.live_event(store, clock, CHAT, first.id)
    second = game.arrive(store, clock, CHAT, source="auto")
    fortune.draw(store, clock, random.Random(1), CONTENT.fortunes, store.get_chat(CHAT), 1)
    for i, status in enumerate(("pending", "ready", "empty", "failed")):
        store.claim_chronicle(CHAT, f"week{i}", status, clock.now())
    report = read_report(path, clock.now())
    m = metrics(report)["2026-11-13"]
    assert m["arrival.manual.normal"] == 1
    assert m["arrival.auto.normal"] == 1
    assert m["arrival.open"] == 1 and m["arrival.alone"] == 1
    assert sum(v for k, v in m.items() if k.startswith("hit.")) == 1
    assert all(m[f"chronicle.{s}"] == 1 for s in ("pending", "ready", "empty", "failed"))
    assert report["local_fortunes"] == [{"chat_id": CHAT, "local_day": "2026-11-14", "count": 1}]
    assert "SECRET-NAME" not in render(report)
    game.delete_unsent_event(store, CHAT, second.id)
    assert "arrival.auto.normal" not in metrics(read_report(path, clock.now()))["2026-11-13"]
    assert store._conn.execute("SELECT COUNT(*) FROM usage_counters").fetchone()[0] == 0


@pytest.mark.parametrize("old_setup", [False, True])
@pytest.mark.parametrize("target", ["none", "setup", "counters", "actors"])
def test_migration_before_setup_and_collision_are_not_a_merge(env, old_setup, target):
    store, clock, usage, path = env
    if old_setup:
        store.enable_chat(CHAT, NOW, "UTC")
    usage.record(CHAT, "command.help", 1)
    if target == "setup":
        store.enable_chat(NEW, NOW, "UTC")
    elif target == "counters":
        usage.record(NEW, "command.stats")
    elif target == "actors":
        usage.record(NEW, user_id=2)
    assert store.migrate_chat(CHAT, NEW) is (target == "none")
    if target == "none":
        assert read_report(path, NOW)["participants"] == {NEW: 1}
        assert not store.migrate_chat(CHAT, NEW)
    else:
        assert store.usage.has_chat(CHAT)
        assert not store.is_enabled(CHAT)
        assert not store.migrate_chat(CHAT, NEW)
        assert store.usage.has_chat(CHAT)
    assert not store.migrate_chat(OTHER, NEW)
    assert not store.migrate_chat(NEW, NEW)


def test_privacy_schema_allowlist_and_best_effort_cleanup(env, caplog):
    store, clock, usage, path = env
    expected = {
        "usage_counters": {"chat_id", "utc_day", "metric", "count"},
        "usage_actors": {"chat_id", "utc_day", "user_id"},
        "usage_meta": {"id", "started_at"},
    }
    for table, columns in expected.items():
        assert store._columns(table) == columns
    usage.record(CHAT, "SECRET COMMAND ARGUMENT", 1)
    assert store._conn.execute("SELECT COUNT(*) FROM usage_counters").fetchone()[0] == 0
    assert "SECRET COMMAND ARGUMENT" not in caplog.text
    usage.record(CHAT, "command.help", 1)
    store._conn.execute(
        "CREATE TEMP TRIGGER deny_cleanup BEFORE DELETE ON usage_actors "
        "BEGIN SELECT RAISE(FAIL, 'unavailable'); END"
    )
    clock.set(NOW + timedelta(days=91))
    usage.maintain()
    assert "usage maintenance failed" in caplog.text
    assert read_report(path, clock.now(), days=90)["participants"] == {}
    # Failure is retried, and a fresh command is still observable.
    store._conn.execute("DROP TRIGGER deny_cleanup")
    usage.maintain()
    usage.record(CHAT, "command.help", 2)
    assert read_report(path, clock.now())["participants"] == {CHAT: 1}


def test_cli_read_only_snapshot_empty_legacy_missing_and_arguments(env, tmp_path):
    store, clock, usage, path = env
    usage.record(CHAT, "command.help", 123456)
    before = path.read_bytes()

    def run(*args):
        return subprocess.run(
            [sys.executable, "-m", "tools.stats", *map(str, args)],
            capture_output=True,
            text=True,
            check=False,
        )

    result = run("--db", path, "--days", 90, "--chat-id", CHAT)
    assert result.returncode == 0, result.stderr
    assert "Начало сбора" in result.stdout and "123456" not in result.stdout
    assert path.read_bytes() == before
    for day in (0, 91, "oops"):
        assert run("--db", path, "--days", day).returncode == 2
    missing = tmp_path / "missing.db"
    assert run("--db", missing).returncode == 2 and not missing.exists()
    legacy = tmp_path / "legacy.db"
    sqlite3.connect(legacy).close()
    old_bytes = legacy.read_bytes()
    assert run("--db", legacy).returncode == 2
    assert legacy.read_bytes() == old_bytes
    with pytest.raises(sqlite3.OperationalError):
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            conn.execute("DELETE FROM usage_counters")
        finally:
            conn.close()
