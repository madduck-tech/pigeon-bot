import random
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from aiogram.exceptions import TelegramMigrateToChat
from aiogram.methods import SendMessage

from pigeon.app import _scheduler_pass
from pigeon.budget import limits
from pigeon.clock import FixedClock
from pigeon.repo.store import Store
from pigeon.scheduler import jobs
from pigeon.services import achievements, game, quests

CHAT = -8001
TZ = "Europe/Moscow"
TUESDAY = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
MONDAY_MIDNIGHT = datetime(2026, 11, 8, 21, 0, tzinfo=UTC)  # Nov 9, 00:00 Moscow
MONDAY_MORNING = datetime(2026, 11, 9, 6, 0, tzinfo=UTC)  # Nov 9, 09:00 Moscow


class FakeSender:
    def __init__(self, failures: int = 0) -> None:
        self.texts: list[tuple[int, str]] = []
        self.failures = failures

    async def send_text(self, chat_id: int, text: str) -> None:
        if self.failures:
            self.failures -= 1
            raise RuntimeError("telegram unavailable")
        self.texts.append((chat_id, text))


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "pigeon.db")
    s.enable_chat(CHAT, TUESDAY, TZ)
    yield s
    s.close()


@pytest.fixture
def clock():
    return FixedClock(TUESDAY)


def _one_hit(store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, event.id, 1, "Anna")


async def test_tick_queues_each_recurring_job_once(store, clock) -> None:
    sender = FakeSender()
    await jobs.tick(store, clock, sender)
    await jobs.tick(store, clock, sender)
    assert store.pending_job(CHAT, jobs.QUEST_CLOSE).run_at == MONDAY_MIDNIGHT
    month = store.pending_job(CHAT, jobs.MONTH_CLOSE).run_at
    assert month == datetime(2026, 11, 30, 21, 0, tzinfo=UTC)  # Dec 1, 00:00 Moscow


async def test_no_jobs_for_chats_not_set_up(store, clock) -> None:
    await jobs.tick(store, clock, FakeSender())
    assert store.pending_job(-1, jobs.QUEST_CLOSE) is None


async def test_report_waits_for_the_end_of_quiet_hours(store, clock) -> None:
    sender = FakeSender()
    await jobs.tick(store, clock, sender)
    _one_hit(store, clock)

    clock.set(MONDAY_MIDNIGHT)
    await jobs.tick(store, clock, sender)
    assert sender.texts == []
    assert store.pending_job(CHAT, jobs.QUEST_CLOSE).run_at == MONDAY_MORNING

    clock.set(MONDAY_MORNING)
    await jobs.tick(store, clock, sender)
    assert len(sender.texts) == 1 and "1/" in sender.texts[0][1]


async def test_next_week_is_queued_after_a_run(store, clock) -> None:
    sender = FakeSender()
    await jobs.tick(store, clock, sender)
    clock.set(MONDAY_MORNING)
    await jobs.tick(store, clock, sender)
    next_run = store.pending_job(CHAT, jobs.QUEST_CLOSE).run_at
    assert next_run == MONDAY_MIDNIGHT + timedelta(days=7)


async def test_pause_drops_the_report_and_still_closes_the_week(store, clock) -> None:
    sender = FakeSender()
    await jobs.tick(store, clock, sender)
    _one_hit(store, clock)
    store.set_paused_until(CHAT, MONDAY_MORNING + timedelta(days=3))
    clock.set(MONDAY_MORNING)
    await jobs.tick(store, clock, sender)
    assert sender.texts == []
    assert store.get_quest(CHAT, "2026-11-02").status == "failed"


async def test_failed_send_is_retried_without_a_second_message(store, clock) -> None:
    sender = FakeSender(failures=1)
    await jobs.tick(store, clock, sender)
    _one_hit(store, clock)
    clock.set(MONDAY_MORNING)
    await jobs.tick(store, clock, sender)
    assert sender.texts == []
    assert store.get_quest(CHAT, "2026-11-02").status == "active"

    clock.set(MONDAY_MORNING + jobs.RETRY_DELAY)
    await jobs.tick(store, clock, sender)
    await jobs.tick(store, clock, sender)
    assert len(sender.texts) == 1
    assert store.get_quest(CHAT, "2026-11-02").status == "failed"


async def test_job_gives_up_and_the_series_continues(store, clock) -> None:
    sender = FakeSender(failures=jobs.MAX_ATTEMPTS)
    await jobs.tick(store, clock, sender)
    _one_hit(store, clock)
    clock.set(MONDAY_MORNING)
    for _ in range(jobs.MAX_ATTEMPTS):
        await jobs.tick(store, clock, sender)
        clock.set(clock.now() + jobs.RETRY_DELAY)
    assert store.pending_job(CHAT, jobs.QUEST_CLOSE).run_at == MONDAY_MIDNIGHT + timedelta(days=7)


async def test_one_failing_chat_does_not_stop_another(store, clock) -> None:
    other = -8002
    store.enable_chat(other, TUESDAY, TZ)

    class FailsForFirst(FakeSender):
        async def send_text(self, chat_id, text):
            if chat_id == CHAT:
                raise RuntimeError("telegram unavailable")
            await super().send_text(chat_id, text)

    sender = FailsForFirst()
    await jobs.tick(store, clock, sender)
    _one_hit(store, clock)
    event = game.arrive(store, clock, other)
    game.hit(store, clock, other, event.id, 2, "Boris")
    clock.set(MONDAY_MORNING)
    await jobs.tick(store, clock, sender)
    assert [chat_id for chat_id, _ in sender.texts] == [other]


async def test_month_close_runs_silently_during_quiet_hours(store, clock) -> None:
    sender = FakeSender()
    clock.set(datetime(2026, 11, 2, 9, 0, tzinfo=UTC))
    await jobs.tick(store, clock, sender)
    store.upsert_member(CHAT, 3, "lurker")
    for _ in range(achievements.FRIEND_MIN_ARRIVALS):
        event = game.arrive(store, clock, CHAT)
        game.hit(store, clock, CHAT, event.id, 1, "Anna")
        clock.set(event.closes_at + timedelta(hours=1))
    midnight = store.pending_job(CHAT, jobs.MONTH_CLOSE).run_at
    clock.set(midnight)
    assert limits.is_quiet(store.get_chat(CHAT), limits.chat_local(store.get_chat(CHAT), midnight))
    await jobs.tick(store, clock, sender)
    assert achievements.FRIEND in store.achievements_of(CHAT, 3)
    assert sender.texts == []


def test_quest_title_reaches_the_dashboard(store, clock) -> None:
    from pigeon.telegram.dashboard import build_text

    for _ in range(quests.TARGET):
        event = game.arrive(store, clock, CHAT)
        game.hit(store, clock, CHAT, event.id, 1, "Anna")
        clock.set(event.closes_at + timedelta(minutes=1))
    text = build_text(store, clock, CHAT)
    assert "Квест недели: ▓▓▓▓▓▓▓▓ 10/10 · титул: Почётный" in text


async def test_timezone_change_does_not_report_the_wrong_week(store, clock) -> None:
    # Regression: a job queued for Monday 00:00 Moscow is Sunday in New York, where the week
    # is still running. It must not report early, and the real Monday must still report.
    sender = FakeSender()
    await jobs.tick(store, clock, sender)
    _one_hit(store, clock)
    store.enable_chat(CHAT, TUESDAY, "America/New_York")
    clock.set(datetime(2026, 11, 8, 21, 0, tzinfo=UTC))  # Sunday 16:00 in New York
    await jobs.tick(store, clock, sender)
    assert sender.texts == []
    clock.set(datetime(2026, 11, 9, 15, 0, tzinfo=UTC))  # Monday 10:00 in New York
    await jobs.tick(store, clock, sender)
    assert len(sender.texts) == 1 and "1/" in sender.texts[0][1]


async def test_week_closed_before_a_timezone_change_is_not_reported_twice(store, clock) -> None:
    store.enable_chat(CHAT, TUESDAY, "Asia/Tokyo")
    sender = FakeSender()
    await jobs.tick(store, clock, sender)
    _one_hit(store, clock)
    clock.set(datetime(2026, 11, 9, 1, 0, tzinfo=UTC))  # Monday 10:00 in Tokyo
    await jobs.tick(store, clock, sender)
    assert len(sender.texts) == 1

    store.enable_chat(CHAT, clock.now(), "America/New_York")  # still Sunday there
    await jobs.tick(store, clock, sender)
    clock.set(datetime(2026, 11, 9, 15, 0, tzinfo=UTC))  # Monday 10:00 in New York
    await jobs.tick(store, clock, sender)
    assert len(sender.texts) == 1


async def test_timezone_change_keeps_a_deferred_report(store, clock) -> None:
    sender = FakeSender()
    await jobs.tick(store, clock, sender)
    _one_hit(store, clock)
    clock.set(MONDAY_MIDNIGHT)
    await jobs.tick(store, clock, sender)  # deferred to 09:00 Moscow
    store.enable_chat(CHAT, MONDAY_MIDNIGHT, "Europe/Berlin")
    clock.set(MONDAY_MORNING + timedelta(hours=3))
    await jobs.tick(store, clock, sender)
    assert len(sender.texts) == 1


async def test_repeating_setup_keeps_a_deferred_report(store, clock) -> None:
    sender = FakeSender()
    await jobs.tick(store, clock, sender)
    _one_hit(store, clock)
    clock.set(MONDAY_MIDNIGHT)
    await jobs.tick(store, clock, sender)  # deferred to 09:00
    store.enable_chat(CHAT, MONDAY_MIDNIGHT, TZ)
    assert store.pending_job(CHAT, jobs.QUEST_CLOSE).run_at == MONDAY_MORNING


async def test_storage_failure_on_one_job_does_not_stop_the_tick(store, clock, monkeypatch) -> None:
    # Regression: an exception here would end the scheduler task, and arrivals with it.
    other = -8003
    store.enable_chat(other, TUESDAY, TZ)
    sender = FakeSender()
    await jobs.tick(store, clock, sender)
    for chat_id, user_id in ((CHAT, 1), (other, 2)):
        event = game.arrive(store, clock, chat_id)
        game.hit(store, clock, chat_id, event.id, user_id, "Anna")
    clock.set(MONDAY_MORNING)

    real_finish = store.finish_job
    failed = []

    def finish(job_id: int) -> None:
        if not failed:
            failed.append(job_id)
            raise sqlite3.OperationalError("database is locked")
        real_finish(job_id)

    monkeypatch.setattr(store, "finish_job", finish)
    await jobs.tick(store, clock, sender)
    assert failed
    assert {chat_id for chat_id, _ in sender.texts} == {CHAT, other}


async def test_overdue_report_deferred_by_quiet_hours_is_not_lost(store, clock) -> None:
    # Regression: the deferral moved the job into another week and the report vanished.
    sender = FakeSender()
    await jobs.tick(store, clock, sender)
    _one_hit(store, clock)
    restart = datetime(2026, 11, 9, 23, 0, tzinfo=UTC)  # Tuesday 02:00 Moscow
    clock.set(restart)
    await jobs.tick(store, clock, sender)
    assert sender.texts == []
    clock.set(restart + timedelta(hours=7))
    await jobs.tick(store, clock, sender)
    assert len(sender.texts) == 1 and "1/" in sender.texts[0][1]


async def test_report_after_a_long_outage_covers_the_last_finished_week(store, clock) -> None:
    # Regression: a job from an old week, deferred over Monday, skipped the week in between.
    sender = FakeSender()
    await jobs.tick(store, clock, sender)
    _one_hit(store, clock)  # week of Nov 2, overdue and never reported
    clock.set(datetime(2026, 11, 11, 9, 0, tzinfo=UTC))  # the bot came back, week of Nov 9
    event = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, event.id, 2, "Boris")
    game.hit(store, clock, CHAT, event.id, 3, "Clara")
    clock.set(datetime(2026, 11, 15, 20, 30, tzinfo=UTC))  # Sunday 23:30, quiet
    store.reschedule_job(store.pending_job(CHAT, jobs.QUEST_CLOSE).id, clock.now())
    await jobs.tick(store, clock, sender)
    clock.set(datetime(2026, 11, 16, 6, 0, tzinfo=UTC))  # Monday 09:00
    await jobs.tick(store, clock, sender)
    assert len(sender.texts) == 1 and "2/" in sender.texts[0][1]
    assert store.get_quest(CHAT, "2026-11-02").status == "failed"


async def test_every_due_job_survives_a_migration_during_the_tick(store, clock) -> None:
    # Regression: the stale list made the next job fail, which skipped a month of `friend`.
    new_chat = -100889

    class Migrated(FakeSender):
        async def send_text(self, chat_id, text):
            if chat_id == CHAT:
                raise TelegramMigrateToChat(SendMessage(chat_id=chat_id, text="x"), "up", new_chat)
            await super().send_text(chat_id, text)

    sender = Migrated()
    clock.set(datetime(2026, 11, 23, 12, 0, tzinfo=UTC))
    await jobs.tick(store, clock, sender)
    store.upsert_member(CHAT, 3, "lurker")
    for _ in range(achievements.FRIEND_MIN_ARRIVALS):
        event = game.arrive(store, clock, CHAT)
        game.hit(store, clock, CHAT, event.id, 1, "Anna")
        clock.set(event.closes_at + timedelta(minutes=1))
    clock.set(datetime(2026, 12, 1, 12, 0, tzinfo=UTC))  # both jobs are due
    await jobs.tick(store, clock, sender)
    await jobs.tick(store, clock, sender)
    assert store.get_chat(CHAT) is None
    assert achievements.FRIEND in store.achievements_of(new_chat, 3)
    assert len(sender.texts) == 1


async def test_refused_migration_stops_calling_the_dead_chat(store, clock) -> None:
    # Regression: one failing Telegram call per tick, forever.
    new_chat = -100890
    store.enable_chat(new_chat, TUESDAY, TZ)
    calls = []

    class Migrated(FakeSender):
        async def send_text(self, chat_id, text):
            calls.append(chat_id)
            raise TelegramMigrateToChat(SendMessage(chat_id=chat_id, text="x"), "up", new_chat)

    sender = Migrated()
    await jobs.tick(store, clock, sender)
    _one_hit(store, clock)
    clock.set(MONDAY_MORNING)
    for _ in range(5):
        await jobs.tick(store, clock, sender)
    assert calls == [CHAT]
    assert not store.is_enabled(CHAT)
    assert store.balance(CHAT, 1) == 1  # nothing was merged or lost


async def test_pause_ending_before_quiet_hours_end_keeps_the_report(store, clock) -> None:
    # Regression: the job ran at midnight while paused and dropped the report.
    sender = FakeSender()
    await jobs.tick(store, clock, sender)
    _one_hit(store, clock)
    store.set_paused_until(CHAT, MONDAY_MORNING - timedelta(hours=4))
    clock.set(MONDAY_MIDNIGHT)
    await jobs.tick(store, clock, sender)
    clock.set(MONDAY_MORNING)
    await jobs.tick(store, clock, sender)
    assert len(sender.texts) == 1


async def test_send_to_a_migrated_chat_moves_it_and_keeps_the_job(store, clock) -> None:
    new_chat = -100888

    class Migrated(FakeSender):
        async def send_text(self, chat_id, text):
            if chat_id == CHAT:
                raise TelegramMigrateToChat(SendMessage(chat_id=chat_id, text="x"), "up", new_chat)
            await super().send_text(chat_id, text)

    sender = Migrated()
    await jobs.tick(store, clock, sender)
    _one_hit(store, clock)
    clock.set(MONDAY_MORNING)
    await jobs.tick(store, clock, sender)
    assert store.get_chat(CHAT) is None
    await jobs.tick(store, clock, sender)
    assert [chat_id for chat_id, _ in sender.texts] == [new_chat]


async def test_scheduler_pass_survives_a_storage_failure(store, clock, monkeypatch) -> None:
    # Regression: an error here ended the scheduler task while polling carried on.
    def locked(now):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "due_jobs", locked)
    await _scheduler_pass(store, clock, random.Random(1), FakeSender(), None)
    # A jobs failure must not cost the chats their arrivals.
    assert store.get_plan(CHAT, "2026-11-03") is not None


async def test_trophy_holder_is_granted_at_midnight_without_any_event(store, clock) -> None:
    # Regression guard: the holder does nothing while the days pass, so no event can trigger it.
    sender = FakeSender()
    await jobs.tick(store, clock, sender)
    _one_hit(store, clock)
    clock.set(TUESDAY + timedelta(days=6))
    await jobs.tick(store, clock, sender)
    assert achievements.TROPHY_HOLDER not in store.achievements_of(CHAT, 1)
    for day in (6, 7, 8):
        clock.set(TUESDAY.replace(hour=21) + timedelta(days=day))
        await jobs.tick(store, clock, sender)
    assert achievements.TROPHY_HOLDER in store.achievements_of(CHAT, 1)
    assert not any("Держатель" in text for _, text in sender.texts)  # granted silently


def test_trophy_time_adds_up_over_separate_stretches(store, clock) -> None:
    for offset, user in ((0, 1), (4, 2), (5, 1), (8, 2)):
        clock.set(TUESDAY + timedelta(days=offset))
        store.set_trophy(CHAT, user, "x", clock.now())
    achievements.check_trophy_holders(store, CHAT, clock.now())
    assert store.achievements_of(CHAT, 1) == [achievements.TROPHY_HOLDER]  # former holder
    assert store.achievements_of(CHAT, 2) == []


def test_daily_trophy_job_is_set_for_the_next_local_midnight(store, clock) -> None:
    jobs.ensure_recurring(store, store.get_chat(CHAT), clock)
    assert store.pending_job(CHAT, jobs.TROPHY_CHECK).run_at == datetime(
        2026, 11, 3, 21, 0, tzinfo=UTC
    )
