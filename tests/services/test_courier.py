import random
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pigeon.clock import FixedClock
from pigeon.content import Fortune, FortuneFormat, _fortunes
from pigeon.repo.store import FortuneDeck, Store
from pigeon.scheduler import courier, fortunes
from pigeon.services import fortune_decks, game
from rigged import CONTENT

CHAT, OTHER = -19, -20
MONDAY = datetime(2026, 11, 2, 6, tzinfo=UTC)  # Moscow 09:00


class Earliest(random.Random):
    def random(self):
        return 0.0

    def randrange(self, *args):
        return 0


class Sender:
    def __init__(self):
        self.sent = []
        self.fail_for = set()

    async def send_text(self, chat_id, text):
        if chat_id in self.fail_for:
            raise RuntimeError("network down")
        self.sent.append((chat_id, text))


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "db")
    s.enable_chat(CHAT, MONDAY, "Europe/Moscow")
    yield s
    s.close()


async def tick(store, at, sender, rng=None, lines=None):
    await courier.tick(
        store, FixedClock(at), rng or Earliest(2), sender, lines or CONTENT.fortunes, CONTENT.names
    )


async def test_full_weeks_random_days_quota_and_restart(tmp_path):
    for seed in range(15):
        path = tmp_path / f"db-{seed}"
        s = Store(path)
        s.enable_chat(CHAT, MONDAY, "Europe/Moscow")
        sender, rng = Sender(), random.Random(seed)
        for day in range(14):
            # Production planner picks a day and time at 09; later tick consumes its saved plan.
            at = MONDAY + timedelta(days=day)
            await tick(s, at, sender, rng)
            plan = s.courier_plan(CHAT, at.date().isoformat())
            s.close()
            s = Store(path)
            if plan.planned_at:
                assert 9 <= plan.planned_at.astimezone(ZoneInfo("Europe/Moscow")).hour < 14
                await tick(s, plan.planned_at, sender, rng)
                await tick(s, plan.planned_at, sender, rng)
        assert len(sender.sent) == 8
        assert s.courier_count(CHAT, "2026-11-02", "2026-11-09") == 4
        assert s.courier_count(CHAT, "2026-11-09", "2026-11-16") == 4
        s.close()


@pytest.mark.parametrize(
    "hour,quiet_start,quiet_end,allowed",
    [
        (8, 23, 9, False),
        (9, 23, 9, True),
        (13, 23, 9, True),
        (14, 23, 9, False),
        (20, 23, 9, False),
        (13, 14, 13, True),
        (13, 23, 14, False),
        (14, 23, 15, False),
        (10, 10, 9, False),
    ],
)
async def test_windows(store, hour, quiet_start, quiet_end, allowed):
    store.set_quiet_hours(CHAT, quiet_start, quiet_end)
    sender = Sender()
    await tick(store, MONDAY.replace(hour=hour - 3), sender)
    assert bool(sender.sent) == allowed


async def test_1359_then_exactly14_and_next_day(store):
    sender = Sender()
    await tick(store, MONDAY.replace(hour=10, minute=59), sender)
    assert len(sender.sent) == 1
    await tick(store, MONDAY.replace(hour=11), sender)
    await tick(store, MONDAY.replace(hour=11) + timedelta(days=1), sender)
    assert len(sender.sent) == 1


async def test_pause_drops_today_but_not_remaining_week(store):
    sender = Sender()
    store.set_paused_until(CHAT, MONDAY + timedelta(hours=2))
    await tick(store, MONDAY, sender)
    await tick(store, MONDAY + timedelta(hours=3), sender)
    assert not sender.sent
    for day in range(1, 7):
        await tick(store, MONDAY + timedelta(days=day), sender)
    assert len(sender.sent) == 4


async def test_failed_send_retry_reopen_rollover_and_history(store, tmp_path):
    sender = Sender()
    sender.fail_for.add(CHAT)
    await tick(store, MONDAY + timedelta(days=6), sender)
    pending = dict(store.pending_courier(CHAT))
    deck = store.get_fortune_deck(CHAT, "daily", 0)
    assert store.fortune_history(CHAT, "2026-11-01", "2026-11-08") == set()
    await tick(store, MONDAY + timedelta(days=6, hours=5), sender)  # exactly14: no attempt
    reopened = Store(tmp_path / "db")
    sender.fail_for.clear()
    await tick(reopened, MONDAY + timedelta(days=7), sender)
    assert sender.sent == [(CHAT, pending["rendered_text"])]
    assert reopened.get_fortune_deck(CHAT, "daily", 0) == deck
    assert reopened.courier_count(CHAT, "2026-11-09", "2026-11-16") == 1
    assert pending["fortune_id"] in reopened.fortune_history(CHAT, "2026-11-09", "2026-11-09")
    assert reopened.pending_courier(CHAT) is None
    reopened.close()


async def test_retry_same_day_same_text_and_mixed_chats(store):
    store.enable_chat(OTHER, MONDAY, "Europe/Moscow")
    sender = Sender()
    sender.fail_for.add(CHAT)
    await tick(store, MONDAY, sender)
    text = store.pending_courier(CHAT)["rendered_text"]
    assert [c for c, _ in sender.sent] == [OTHER]
    sender.fail_for.clear()
    await tick(store, MONDAY + timedelta(minutes=1), sender)
    assert sender.sent[-1] == (CHAT, text)
    assert len(sender.sent) == 2


async def test_game_and_monday_independent(store):
    clock = FixedClock(MONDAY)
    event = game.arrive(store, clock, CHAT, rng=random.Random(5))
    deck = store.get_pigeon_deck(CHAT)
    sender = Sender()
    await tick(store, MONDAY, sender)
    assert store.current_open_event(CHAT) == event
    assert store.get_pigeon_deck(CHAT) == deck
    assert store.count_auto_events_since(CHAT, MONDAY) == 0
    assert store._conn.execute("SELECT COUNT(*) FROM points_ledger").fetchone()[0] == 0
    assert "сразу улетел" in sender.sent[0][1]
    assert "Попаданий" not in sender.sent[0][1]
    await fortunes.tick(
        store,
        FixedClock(MONDAY + timedelta(hours=1)),
        random.Random(4),
        sender,
        CONTENT.fortunes,
        CONTENT.names,
        CONTENT.quests,
    )
    assert len(sender.sent) == 2
    assert "Новая неделя" in sender.sent[1][1]


async def test_personal_excludes_published_but_not_queue(store):
    # A small independent pool makes any failure to consult courier history observable.
    pool = [Fortune(f"f-{i}", FortuneFormat.CLASSIC, f"text {i}") for i in range(3)]
    sender = Sender()
    await tick(store, MONDAY, sender, lines=pool)
    published = store._conn.execute("SELECT fortune_id FROM courier_fortunes").fetchone()[0]
    fortune_decks.prepare(store, CHAT, 1, "2026-11-02", pool, ["Гена"], Earliest())
    assert store.get_fortune_issue(CHAT, 1, "2026-11-02").fortune_id != published
    assert published not in store.fortune_history(OTHER, "2026-11-02", "2026-11-02")
    assert published not in store.fortune_history(CHAT, "2026-11-03", "2026-11-16")


async def test_upgrade_fifo_cancel_serial_and_keep_monday(store):
    with store.transaction():
        for day, ident in [
            ("01", "arbitrary-chain-id"),
            ("02", CONTENT.fortunes[0].id),
            ("03", CONTENT.fortunes[1].id),
            ("04", None),
        ]:
            store.save_fortune_issue(CHAT, 0, f"2026-10-{day}", ident, f"body-{day}")
        store.save_fortune_deck(
            CHAT, "daily", 0, FortuneDeck(1, ["arbitrary-chain-id"], ["second-custom-id"], None, [])
        )
    fortune_decks.migrate_courier(store, CONTENT.fortunes)
    fortune_decks.migrate_courier(store, CONTENT.fortunes)
    assert store.get_fortune_issue(CHAT, 0, "2026-10-04").rendered_text == "body-04"
    assert store.get_fortune_issue(CHAT, 0, "2026-10-01") is None
    assert store.get_fortune_deck(CHAT, "daily", 0).forced == []
    sender = Sender()
    await tick(store, MONDAY, sender)
    await tick(store, MONDAY + timedelta(days=1), sender)
    assert "body-02" in sender.sent[0][1]
    assert "body-03" in sender.sent[1][1]
    assert store.pending_courier(CHAT) is None


def test_reject_all_serial_inputs():
    for raw in [
        dict(id="x", format="serial", text="x"),
        dict(id="x", format="classic", text="x", part=1),
        dict(id="x", format="classic", text="x", next="y"),
    ]:
        with pytest.raises(ValueError):
            _fortunes([raw])


def test_queue_failure_rolls_back_draw(store, monkeypatch):
    fortune_decks.migrate_courier(store, CONTENT.fortunes)

    def fail(*args):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "enqueue_courier", fail)
    with pytest.raises(RuntimeError):
        fortune_decks.prepare_courier(
            store, CHAT, "2026-11-02", CONTENT.fortunes, CONTENT.names, random.Random(1)
        )
    assert store.get_fortune_deck(CHAT, "daily", 0) is None
    assert store.pending_courier(CHAT) is None


async def test_supergroup_migration_moves_queue_plans_history(store):
    sender = Sender()
    sender.fail_for.add(CHAT)
    await tick(store, MONDAY, sender)
    assert store.migrate_chat(CHAT, OTHER)
    sender.fail_for.clear()
    await tick(store, MONDAY, sender)
    assert [c for c, _ in sender.sent] == [OTHER]
    assert store.courier_published(OTHER, "2026-11-02")
    assert store.courier_plan(OTHER, "2026-11-02").status == "fired"
    assert store.pending_courier(CHAT) is None


@pytest.mark.parametrize("change", ["pause", "quiet", "timezone"])
async def test_settings_changed_during_other_chats_send(store, change):
    store.enable_chat(OTHER, MONDAY, "Europe/Moscow")
    order = [c.chat_id for c in store.enabled_chats()]
    first, second = order

    class Changed(Sender):
        async def send_text(self, chat_id, text):
            await super().send_text(chat_id, text)
            if chat_id == first:
                if change == "pause":
                    store.set_paused_until(second, MONDAY + timedelta(hours=2))
                elif change == "quiet":
                    store.set_quiet_hours(second, 23, 15)
                else:
                    store.enable_chat(second, MONDAY, "Asia/Tokyo")  # already15:00

    sender = Changed()
    await tick(store, MONDAY, sender)
    assert [chat for chat, _ in sender.sent] == [first]


async def test_confirmation_day_after_network_delay(store):
    clock = FixedClock(MONDAY.replace(hour=10, minute=59))  # 13:59 local

    class Slow(Sender):
        async def send_text(self, chat_id, text):
            await super().send_text(chat_id, text)
            clock.set(MONDAY + timedelta(days=1))

    sender = Slow()
    await courier.tick(store, clock, Earliest(), sender, CONTENT.fortunes, CONTENT.names)
    assert store.courier_published(CHAT, "2026-11-03")
    assert not store.courier_published(CHAT, "2026-11-02")
    assert store.courier_plan(CHAT, "2026-11-02").status == "fired"
    await courier.tick(store, clock, Earliest(), sender, CONTENT.fortunes, CONTENT.names)
    assert len(sender.sent) == 1


def test_confirmation_failure_is_atomic(store, monkeypatch):
    fortune_decks.migrate_courier(store, CONTENT.fortunes)
    pending = fortune_decks.prepare_courier(
        store, CHAT, "2026-11-02", CONTENT.fortunes, CONTENT.names, random.Random(1)
    )

    def fail(*args):
        raise RuntimeError("disk failure after history insert")

    monkeypatch.setattr(store, "save_courier_plan", fail)
    with pytest.raises(RuntimeError):
        store.confirm_courier(CHAT, "2026-11-02", "2026-11-02", pending["id"], MONDAY)
    assert not store.courier_published(CHAT, "2026-11-02")
    assert store.pending_courier(CHAT)["id"] == pending["id"]


def test_queue_is_not_personal_history(store):
    fortune_decks.migrate_courier(store, CONTENT.fortunes)
    pending = fortune_decks.prepare_courier(
        store, CHAT, "2026-11-02", CONTENT.fortunes, CONTENT.names, random.Random(7)
    )
    fortune_decks.prepare(
        store, CHAT, 9, "2026-11-02", CONTENT.fortunes, CONTENT.names, random.Random(7)
    )
    assert store.get_fortune_issue(CHAT, 9, "2026-11-02").fortune_id == pending["fortune_id"]


@pytest.mark.parametrize(
    "timezone,at",
    [
        ("UTC", datetime(2026, 11, 2, 9, tzinfo=UTC)),
        ("Europe/Berlin", datetime(2026, 3, 29, 7, tzinfo=UTC)),  # DST starts
        ("Europe/Berlin", datetime(2026, 10, 25, 8, tzinfo=UTC)),  # DST ends
        ("Asia/Tokyo", datetime(2026, 11, 1, 0, tzinfo=UTC)),
    ],
)
async def test_local_windows_across_timezones(store, timezone, at):
    store.enable_chat(CHAT, MONDAY, timezone)
    sender = Sender()
    await tick(store, at, sender)
    assert len(sender.sent) == 1
    local_day = at.astimezone(ZoneInfo(timezone)).date().isoformat()
    assert store.courier_published(CHAT, local_day)


async def test_app_wires_courier_and_isolates_its_failure(store, monkeypatch):
    from pigeon import app

    calls = []

    async def quiet(*args):
        pass

    async def failing(*args):
        calls.append("courier")
        raise RuntimeError("courier down")

    async def summary(*args):
        calls.append("summary")

    monkeypatch.setattr(app.arrivals, "tick", quiet)
    monkeypatch.setattr(app.courier, "tick", failing)
    monkeypatch.setattr(app.fortunes, "tick", summary)
    await app._scheduler_pass(store, FixedClock(MONDAY), random.Random(1), Sender(), CONTENT)
    assert calls == ["courier", "summary"]


async def test_database_preparation_crossing14_does_not_send(store, monkeypatch):
    sender = Sender()
    sender.fail_for.add(CHAT)
    await tick(store, MONDAY, sender)
    assert store.pending_courier(CHAT) is not None
    clock = FixedClock(MONDAY.replace(hour=10, minute=59, second=59, microsecond=999000))
    original = fortune_decks.prepare_courier

    def delayed(*args):
        pending = original(*args)
        clock.set(clock.now() + timedelta(milliseconds=2))
        return pending

    monkeypatch.setattr(fortune_decks, "prepare_courier", delayed)
    sender.fail_for.clear()
    await courier.tick(store, clock, Earliest(), sender, CONTENT.fortunes, CONTENT.names)
    assert not sender.sent
    assert store.pending_courier(CHAT) is not None
    assert store.courier_plan(CHAT, "2026-11-02").status == "skipped"


async def test_latest_plan_is_reached_by30_second_scheduler(store):
    class Latest(Earliest):
        def randrange(self, stop, *args):
            return stop - 1

    sender = Sender()
    for offset in range(7):
        morning = MONDAY + timedelta(days=offset)
        await tick(store, morning, sender, rng=Latest())
        # Tick phase deliberately at :45; planner must leave enough time before14.
        await tick(
            store, morning + timedelta(hours=4, minutes=59, seconds=45), sender, rng=Latest()
        )
        await tick(store, morning + timedelta(hours=5, seconds=15), sender, rng=Latest())
    assert len(sender.sent) == 4
