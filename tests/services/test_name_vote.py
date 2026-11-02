import random
from datetime import UTC, datetime, timedelta

import pytest
from aiogram.exceptions import TelegramMigrateToChat
from aiogram.methods import SendMessage

from pigeon.clock import FixedClock
from pigeon.render import texts
from pigeon.repo.store import Store
from pigeon.scheduler import jobs
from pigeon.services import name_vote

CHAT = -7001
TZ = "Europe/Moscow"
OCT_20 = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
FIRST_MORNING = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)  # Nov 1, 09:00 Moscow
FIRST_MIDNIGHT = datetime(2026, 10, 31, 21, 0, tzinfo=UTC)  # Nov 1, 00:00 Moscow
POOL = ("Гена", "Толик", "Валера", "Серёга", "Саня", "Колян", "Жора")


class FakeSender:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.polls: list[tuple[str, ...]] = []
        self.stopped: list[int] = []
        self.counts: list[int] | None = None
        self.fail_polls = 0
        self.fail_texts = 0

    async def send_text(self, chat_id: int, text: str) -> None:
        if self.fail_texts:
            self.fail_texts -= 1
            raise RuntimeError("telegram unavailable")
        self.texts.append(text)

    async def send_poll(self, chat_id: int, question: str, options) -> int:
        if self.fail_polls:
            self.fail_polls -= 1
            raise RuntimeError("telegram unavailable")
        self.polls.append(tuple(options))
        return 100 + len(self.polls)

    async def stop_poll(self, chat_id: int, message_id: int) -> list[int] | None:
        self.stopped.append(message_id)
        if self.counts is None:
            return None
        return self.counts


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "pigeon.db")
    s.enable_chat(CHAT, OCT_20, TZ)
    yield s
    s.close()


@pytest.fixture
def clock():
    return FixedClock(OCT_20)


@pytest.fixture
def sender():
    return FakeSender()


async def _tick(store, clock, sender, rng=None) -> None:
    await jobs.tick(store, clock, sender, rng or random.Random(1), POOL)


async def _open_vote(store, clock, sender) -> None:
    outcome = await name_vote.start(store, clock, random.Random(1), sender, POOL, CHAT)
    assert outcome is name_vote.Start.STARTED


async def _close_vote(store, clock, sender, counts, rng=None) -> None:
    sender.counts = counts
    await _tick(store, clock, sender, rng)  # queues the closing job
    clock.set(store.open_name_vote(CHAT).closes_at)
    await _tick(store, clock, sender, rng)


async def test_poll_offers_five_different_names(store, clock, sender) -> None:
    await _open_vote(store, clock, sender)
    assert len(sender.polls[0]) == name_vote.OPTIONS == len(set(sender.polls[0]))
    assert store.open_name_vote(CHAT).message_id == 101


async def test_one_open_vote_at_a_time(store, clock, sender) -> None:
    await _open_vote(store, clock, sender)
    outcome = await name_vote.start(store, clock, random.Random(1), sender, POOL, CHAT)
    assert outcome is name_vote.Start.ALREADY
    assert len(sender.polls) == 1


def test_used_names_are_not_offered(store, clock) -> None:
    store.close_name_vote(
        CHAT,
        store.create_name_vote(CHAT, ["Гена"], OCT_20, OCT_20),
        "Гена",
        texts.PIGEON_NAME,
        OCT_20,
    )
    free = name_vote.candidates(store, random.Random(1), ("Гена", texts.PIGEON_NAME, "Толик"), CHAT)
    assert free == ["Толик"]


async def test_too_few_names_means_no_poll(store, clock, sender) -> None:
    outcome = await name_vote.start(store, clock, random.Random(1), sender, ("Гена",), CHAT)
    assert outcome is name_vote.Start.NO_OPTIONS
    assert sender.polls == [] and store.open_name_vote(CHAT) is None


async def test_two_free_names_are_enough(store, clock, sender) -> None:
    outcome = await name_vote.start(store, clock, random.Random(1), sender, POOL[:2], CHAT)
    assert outcome is name_vote.Start.STARTED and len(sender.polls[0]) == 2


async def test_failed_send_leaves_no_vote(store, clock, sender) -> None:
    sender.fail_polls = 1
    with pytest.raises(RuntimeError):
        await name_vote.start(store, clock, random.Random(1), sender, POOL, CHAT)
    assert store.open_name_vote(CHAT) is None
    await _open_vote(store, clock, sender)  # not blocked by the failed attempt


async def test_vote_is_scheduled_for_the_first_and_waits_for_morning(store, clock, sender) -> None:
    await _tick(store, clock, sender)
    assert store.pending_job(CHAT, jobs.NAME_VOTE_OPEN).run_at == FIRST_MIDNIGHT
    clock.set(FIRST_MIDNIGHT)
    await _tick(store, clock, sender)
    assert sender.polls == []
    assert store.pending_job(CHAT, jobs.NAME_VOTE_OPEN).run_at == FIRST_MORNING
    clock.set(FIRST_MORNING)
    await _tick(store, clock, sender)
    assert len(sender.polls) == 1
    next_month = datetime(2026, 11, 30, 21, 0, tzinfo=UTC)
    assert store.pending_job(CHAT, jobs.NAME_VOTE_OPEN).run_at == next_month


async def test_pause_postpones_the_monthly_vote(store, clock, sender) -> None:
    await _tick(store, clock, sender)
    resume = FIRST_MORNING + timedelta(days=2)
    store.set_paused_until(CHAT, resume)
    clock.set(FIRST_MORNING)
    await _tick(store, clock, sender)
    assert sender.polls == []
    assert store.pending_job(CHAT, jobs.NAME_VOTE_OPEN).run_at <= resume
    clock.set(resume)
    await _tick(store, clock, sender)
    assert len(sender.polls) == 1


async def test_failed_automatic_poll_is_retried(store, clock, sender) -> None:
    await _tick(store, clock, sender)
    sender.fail_polls = 1
    clock.set(FIRST_MORNING)
    await _tick(store, clock, sender)
    assert sender.polls == []
    clock.set(FIRST_MORNING + jobs.RETRY_DELAY)
    await _tick(store, clock, sender)
    assert len(sender.polls) == 1


async def test_recent_manual_vote_skips_the_monthly_one(store, clock, sender) -> None:
    await _tick(store, clock, sender)
    clock.set(FIRST_MORNING - timedelta(days=2))
    await _open_vote(store, clock, sender)
    await _close_vote(store, clock, sender, [1, 0, 0, 0, 0])
    clock.set(FIRST_MORNING)
    await _tick(store, clock, sender)
    assert len(sender.polls) == 1  # §9: at most one vote a week


async def test_winner_renames_the_pigeon_and_the_old_name_is_logged(store, clock, sender) -> None:
    await _open_vote(store, clock, sender)
    options = sender.polls[0]
    await _close_vote(store, clock, sender, [0, 3, 1, 0, 0])
    assert store.get_pigeon(CHAT).name == options[1]
    assert store.past_names(CHAT) == {texts.PIGEON_NAME}
    assert sender.texts == [texts.NAME_RENAMED.format(name=options[1])]
    assert sender.stopped == [101]
    assert store.open_name_vote(CHAT) is None


async def test_poll_is_not_closed_early(store, clock, sender) -> None:
    await _open_vote(store, clock, sender)
    sender.counts = [1, 0, 0, 0, 0]
    await _tick(store, clock, sender)
    clock.set(OCT_20 + name_vote.VOTE_TIME - timedelta(seconds=1))
    await _tick(store, clock, sender)
    assert sender.stopped == []


def test_tie_is_broken_among_the_leaders_only() -> None:
    options = ("a", "b", "c")
    winners = {name_vote.pick_winner(random.Random(seed), options, [2, 2, 1]) for seed in range(30)}
    assert winners == {"a", "b"}


async def test_no_votes_keeps_the_name(store, clock, sender) -> None:
    await _open_vote(store, clock, sender)
    await _close_vote(store, clock, sender, [0, 0, 0, 0, 0])
    assert store.get_pigeon(CHAT).name is None
    assert store.past_names(CHAT) == set()
    assert sender.texts == [texts.NAME_NO_VOTES.format(name=texts.PIGEON_NAME)]


async def test_deleted_poll_ends_the_vote_silently(store, clock, sender) -> None:
    await _open_vote(store, clock, sender)
    await _close_vote(store, clock, sender, None)
    assert store.open_name_vote(CHAT) is None
    assert store.get_pigeon(CHAT).name is None
    assert sender.texts == []


async def test_closing_waits_for_the_end_of_quiet_hours(store, clock, sender) -> None:
    clock.set(datetime(2026, 11, 3, 20, 30, tzinfo=UTC))  # 23:30 Moscow
    await _open_vote(store, clock, sender)
    await _tick(store, clock, sender)
    sender.counts = [1, 0, 0, 0, 0]
    clock.set(store.open_name_vote(CHAT).closes_at)  # 23:30 the next day
    await _tick(store, clock, sender)
    assert sender.stopped == [] and sender.texts == []
    clock.set(datetime(2026, 11, 5, 6, 0, tzinfo=UTC))  # 09:00 Moscow
    await _tick(store, clock, sender)
    assert len(sender.texts) == 1


async def test_pause_renames_without_a_message(store, clock, sender) -> None:
    await _open_vote(store, clock, sender)
    await _tick(store, clock, sender)
    store.set_paused_until(CHAT, OCT_20 + timedelta(days=7))
    options = sender.polls[0]
    await _close_vote(store, clock, sender, [1, 0, 0, 0, 0])
    assert store.get_pigeon(CHAT).name == options[0]
    assert sender.texts == []
    assert store.unreported_name_vote(CHAT) is None


async def test_failed_announcement_is_retried_with_the_same_result(store, clock, sender) -> None:
    # Regression guard: a poll can be stopped once, so the retry must not need it again.
    await _open_vote(store, clock, sender)
    options = sender.polls[0]
    sender.fail_texts = 1
    await _close_vote(store, clock, sender, [0, 0, 5, 0, 0])
    assert sender.texts == []
    clock.set(clock.now() + jobs.RETRY_DELAY)
    await _tick(store, clock, sender)
    await _tick(store, clock, sender)
    assert sender.texts == [texts.NAME_RENAMED.format(name=options[2])]
    assert sender.stopped == [101]
    assert store.past_names(CHAT) == {texts.PIGEON_NAME}


async def test_second_rename_logs_the_period_of_the_previous_name(store, clock, sender) -> None:
    await _open_vote(store, clock, sender)
    first = sender.polls[0][0]
    await _close_vote(store, clock, sender, [1, 0, 0, 0, 0])
    clock.set(clock.now() + timedelta(days=30))
    await _open_vote(store, clock, sender)
    await _close_vote(store, clock, sender, [1, 0, 0, 0, 0])
    assert store.past_names(CHAT) == {texts.PIGEON_NAME, first}
    assert first not in sender.polls[1]


async def test_open_vote_moves_with_a_migrated_chat(store, clock, sender) -> None:
    new_chat = -7002
    await _open_vote(store, clock, sender)
    assert store.migrate_chat(CHAT, new_chat)
    assert store.open_name_vote(new_chat).message_id == 101


async def test_migration_during_the_monthly_poll_keeps_the_job(store, clock, sender) -> None:
    new_chat = -7003

    class Migrated(FakeSender):
        async def send_poll(self, chat_id, question, options):
            if chat_id == CHAT:
                raise TelegramMigrateToChat(SendMessage(chat_id=chat_id, text="x"), "up", new_chat)
            return await super().send_poll(chat_id, question, options)

    migrated = Migrated()
    await _tick(store, clock, migrated)
    clock.set(FIRST_MORNING)
    await _tick(store, clock, migrated)
    assert store.get_chat(CHAT) is None
    await _tick(store, clock, migrated)
    assert len(migrated.polls) == 1 and store.open_name_vote(new_chat) is not None


async def test_votes_of_two_chats_do_not_mix(store, clock, sender) -> None:
    other = -7004
    store.enable_chat(other, OCT_20, TZ)
    await _open_vote(store, clock, sender)
    assert store.open_name_vote(other) is None
    await _close_vote(store, clock, sender, [1, 0, 0, 0, 0])
    assert store.get_pigeon(other).name is None


async def test_pause_off_releases_the_postponed_vote(store, clock, sender) -> None:
    await _tick(store, clock, sender)
    store.set_paused_until(CHAT, FIRST_MORNING + timedelta(days=7))
    clock.set(FIRST_MORNING)
    await _tick(store, clock, sender)
    store.set_paused_until(CHAT, None)
    clock.set(FIRST_MORNING + jobs.PAUSE_RECHECK)
    await _tick(store, clock, sender)
    assert len(sender.polls) == 1


async def test_retry_of_an_old_close_job_leaves_the_next_vote_open(store, clock, sender) -> None:
    await _open_vote(store, clock, sender)
    sender.fail_texts = 1
    await _close_vote(store, clock, sender, [1, 0, 0, 0, 0])
    await _open_vote(store, clock, sender)  # an admin starts another one before the retry
    sender.counts = [0, 1, 0, 0, 0]
    clock.set(clock.now() + jobs.RETRY_DELAY)
    await _tick(store, clock, sender)
    await _tick(store, clock, sender)
    assert store.open_name_vote(CHAT) is not None
    assert sender.stopped == [101]
    assert len(sender.texts) == 1  # the first vote's result still got out


async def test_close_job_left_from_a_failed_send_does_not_close_a_new_vote(
    store, clock, sender
) -> None:
    store.ensure_job(CHAT, jobs.NAME_VOTE_CLOSE, OCT_20 + timedelta(hours=1))
    await _open_vote(store, clock, sender)
    clock.set(OCT_20 + timedelta(hours=1))
    await _tick(store, clock, sender)
    assert store.open_name_vote(CHAT) is not None and sender.stopped == []


async def test_announcement_survives_a_twenty_minute_outage(store, clock, sender) -> None:
    await _open_vote(store, clock, sender)
    sender.fail_texts = jobs.MAX_ATTEMPTS
    await _close_vote(store, clock, sender, [1, 0, 0, 0, 0])
    for _ in range(12):
        clock.set(clock.now() + timedelta(hours=1))
        await _tick(store, clock, sender)
    assert len(sender.texts) == 1


async def test_retries_are_few_and_spread_out(store, clock, sender) -> None:
    await _open_vote(store, clock, sender)
    sender.fail_texts = 100
    await _close_vote(store, clock, sender, [1, 0, 0, 0, 0])
    delays = []
    for _ in range(jobs.NAME_VOTE_CLOSE_ATTEMPTS - 2):
        job = store.pending_job(CHAT, jobs.NAME_VOTE_CLOSE)
        delays.append(job.run_at - clock.now())
        clock.set(job.run_at)
        await _tick(store, clock, sender)
    assert delays[:3] == [jobs.RETRY_DELAY * 2**n for n in range(3)]


async def _give_up(store, clock, sender) -> None:
    await _tick(store, clock, sender)
    clock.set(store.open_name_vote(CHAT).closes_at)
    for _ in range(72):  # hourly for three days: past every retry and quiet hours
        await _tick(store, clock, sender)
        clock.set(clock.now() + timedelta(hours=1))


class Kicked(FakeSender):
    async def stop_poll(self, chat_id, message_id):
        self.stopped.append(message_id)
        raise RuntimeError("bot was kicked")


async def test_vote_is_given_up_and_nothing_surfaces_later(store, clock) -> None:
    kicked = Kicked()
    await _open_vote(store, clock, kicked)
    await _give_up(store, clock, kicked)
    assert len(kicked.stopped) == jobs.NAME_VOTE_CLOSE_ATTEMPTS
    assert store.open_name_vote(CHAT) is None
    assert store.unreported_name_vote(CHAT) is None
    assert store.pending_job(CHAT, jobs.NAME_VOTE_CLOSE) is None


def test_giving_up_leaves_a_vote_that_is_not_due(store, clock) -> None:
    vote_id = store.create_name_vote(CHAT, ["a", "b"], OCT_20, OCT_20 + name_vote.VOTE_TIME)
    name_vote.abandon(store, CHAT, OCT_20 + timedelta(minutes=15))
    assert store.open_name_vote(CHAT).id == vote_id


async def test_newer_result_supersedes_an_unannounced_older_one(store, clock, sender) -> None:
    await _open_vote(store, clock, sender)
    sender.fail_texts = 1
    await _close_vote(store, clock, sender, [1, 0, 0, 0, 0])
    first = store.unreported_name_vote(CHAT)
    clock.set(clock.now() + timedelta(days=2))
    store.finish_job(store.pending_job(CHAT, jobs.NAME_VOTE_CLOSE).id)
    await _open_vote(store, clock, sender)
    await _close_vote(store, clock, sender, [1, 0, 0, 0, 0])
    assert first is not None and store.unreported_name_vote(CHAT) is None
    assert len(sender.texts) == 1
