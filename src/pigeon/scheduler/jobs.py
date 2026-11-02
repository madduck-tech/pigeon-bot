"""Scheduled jobs: recurring work kept in the `jobs` table, so it survives a restart.

Each recurring kind is re-created by ensure_recurring after the previous run finishes or
fails, so one failed run loses that run only, not the whole series. A job that posts waits
out quiet hours and stays silent during a pause; a silent job runs whenever it is due. A job
closes the last period finished at the moment it runs, so deferrals, downtime and a timezone
change need no special handling, and reporting twice is prevented in the quest row.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from aiogram.exceptions import TelegramMigrateToChat

from pigeon.budget import limits
from pigeon.clock import Clock
from pigeon.repo.store import ChatSettings, Job, Store
from pigeon.services import achievements, name_vote, quests

log = logging.getLogger("pigeon.jobs")

QUEST_CLOSE = "quest_close"
MONTH_CLOSE = "month_close"
TROPHY_CHECK = "trophy_check"
NAME_VOTE_OPEN = "name_vote_open"
NAME_VOTE_CLOSE = "name_vote_close"
PUBLISHING_KINDS = {QUEST_CLOSE, NAME_VOTE_OPEN, NAME_VOTE_CLOSE}
# A monthly vote would be lost for the whole month, so a pause postpones it instead.
WAITS_FOR_PAUSE = {NAME_VOTE_OPEN}

MAX_ATTEMPTS = 5
RETRY_DELAY = timedelta(minutes=5)
# A poll can be stopped only once and its result is announced once, so closing a vote waits out
# an outage: the delay doubles, which covers about 10 hours in few attempts. Few matters, as a
# delivered message whose response was lost is sent again by every retry.
NAME_VOTE_CLOSE_ATTEMPTS = 8
PAUSE_RECHECK = timedelta(hours=1)  # a pause can be lifted early with /pigeon pause off


class JobSender(Protocol):
    async def send_text(self, chat_id: int, text: str) -> None: ...

    async def send_poll(self, chat_id: int, question: str, options: Sequence[str]) -> int: ...

    async def stop_poll(self, chat_id: int, message_id: int) -> list[int] | None: ...


@dataclass(frozen=True)
class Context:
    store: Store
    clock: Clock
    sender: JobSender
    rng: random.Random
    names: Sequence[str]


async def _quest_close(ctx: Context, chat: ChatSettings, now) -> None:
    key = quests.last_finished_week(chat, now)
    report = quests.week_report(ctx.store, chat, key)
    if report is not None and not limits.is_paused(chat, now):
        await ctx.sender.send_text(chat.chat_id, report)
    quests.close_week(ctx.store, chat, key)
    ctx.store.mark_dashboard_dirty(chat.chat_id)


async def _month_close(ctx: Context, chat: ChatSettings, now) -> None:
    achievements.close_month(ctx.store, chat, now)


async def _trophy_check(ctx: Context, chat: ChatSettings, now) -> None:
    achievements.check_trophy_holders(ctx.store, chat.chat_id, now)


async def _name_vote_open(ctx: Context, chat: ChatSettings, now) -> None:
    if not name_vote.too_soon(ctx.store, chat.chat_id, now):
        await name_vote.start(ctx.store, ctx.clock, ctx.rng, ctx.sender, ctx.names, chat.chat_id)


async def _name_vote_close(ctx: Context, chat: ChatSettings, now) -> None:
    # Bound to the vote's own deadline: a job left from an earlier vote must not close this one.
    vote = ctx.store.open_name_vote(chat.chat_id)
    if vote is not None and now >= vote.closes_at:
        counts = None
        if vote.message_id is not None:
            counts = await ctx.sender.stop_poll(chat.chat_id, vote.message_id)
        name_vote.close(ctx.store, ctx.rng, vote, counts, now)
    # Separate from closing: a retry after a failed send finds the stored result here.
    closed = ctx.store.unreported_name_vote(chat.chat_id)
    if closed is None:
        return
    if not limits.is_paused(chat, now):
        await ctx.sender.send_text(chat.chat_id, name_vote.report(ctx.store, closed))
    ctx.store.mark_name_vote_reported(chat.chat_id, closed.id)


HANDLERS = {
    QUEST_CLOSE: _quest_close,
    MONTH_CLOSE: _month_close,
    TROPHY_CHECK: _trophy_check,
    NAME_VOTE_OPEN: _name_vote_open,
    NAME_VOTE_CLOSE: _name_vote_close,
}


def ensure_recurring(store: Store, chat: ChatSettings, clock: Clock) -> None:
    local = limits.chat_local(chat, clock.now())
    store.ensure_job(chat.chat_id, QUEST_CLOSE, limits.next_week_start_utc(local))
    store.ensure_job(chat.chat_id, MONTH_CLOSE, limits.next_month_start_utc(local))
    store.ensure_job(chat.chat_id, TROPHY_CHECK, limits.next_day_start_utc(local))
    store.ensure_job(chat.chat_id, NAME_VOTE_OPEN, limits.next_month_start_utc(local))
    # Found from the vote itself, so a vote an admin started gets closed the same way.
    vote = store.open_name_vote(chat.chat_id)
    if vote is not None:
        store.ensure_job(chat.chat_id, NAME_VOTE_CLOSE, vote.closes_at)


async def _run(ctx: Context, job: Job) -> None:
    store = ctx.store
    chat = store.get_chat(job.chat_id)
    if chat is None:
        return  # the chat migrated earlier in this tick; the job moved with it
    now = ctx.clock.now()
    if job.kind in PUBLISHING_KINDS:
        local = limits.chat_local(chat, now)
        if limits.is_quiet(chat, local):
            store.reschedule_job(job.id, limits.quiet_end_after(chat, local))
            return
    if job.kind in WAITS_FOR_PAUSE and limits.is_paused(chat, now):
        store.reschedule_job(job.id, min(chat.paused_until, now + PAUSE_RECHECK))
        return
    try:
        await HANDLERS[job.kind](ctx, chat, now)
    except TelegramMigrateToChat as exc:
        log.warning("chat %s moved to %s", chat.chat_id, exc.migrate_to_chat_id)
        store.migrate_chat(chat.chat_id, exc.migrate_to_chat_id)
        return  # the job moved with the chat, or the old chat was switched off
    except Exception:
        log.exception("job %s (%s) failed in chat %s", job.id, job.kind, job.chat_id)
        closing_vote = job.kind == NAME_VOTE_CLOSE
        if job.attempts + 1 >= (NAME_VOTE_CLOSE_ATTEMPTS if closing_vote else MAX_ATTEMPTS):
            store.fail_job(job.id)
            if closing_vote:
                name_vote.abandon(store, chat.chat_id, now)
        else:
            store.retry_job(job.id, now + RETRY_DELAY * (2**job.attempts if closing_vote else 1))
        return
    store.finish_job(job.id)


async def tick(
    store: Store,
    clock: Clock,
    sender: JobSender,
    rng: random.Random | None = None,
    names: Sequence[str] = (),
) -> None:
    """Run every due job, then make sure each chat has its next ones queued."""
    ctx = Context(store, clock, sender, rng or random.Random(), names)
    for job in store.due_jobs(clock.now()):
        try:
            await _run(ctx, job)
        except Exception:
            log.exception("job %s could not be recorded", job.id)
    for chat in store.enabled_chats():
        try:
            ensure_recurring(store, chat, clock)
        except Exception:
            log.exception("chat %s: could not queue jobs", chat.chat_id)
