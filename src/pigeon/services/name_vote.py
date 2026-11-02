"""The vote for the pigeon's name: open a poll, close it a day later, rename the winner.

The result is stored when the poll closes, before it is announced: a closed poll cannot be read
a second time, so a failed announcement has to be retried from the database.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import datetime, timedelta
from enum import Enum
from typing import Protocol

from pigeon.clock import Clock
from pigeon.render import texts
from pigeon.repo.store import NameVote, Store

OPTIONS = 5
MIN_OPTIONS = 2
VOTE_TIME = timedelta(hours=24)
MIN_GAP = timedelta(days=7)  # scheduled messages of one type go out at most weekly


class PollSender(Protocol):
    async def send_poll(self, chat_id: int, question: str, options: Sequence[str]) -> int: ...


class Start(Enum):
    STARTED = "started"
    ALREADY = "already"
    NO_OPTIONS = "no_options"


def current_name(store: Store, chat_id: int) -> str:
    return store.get_pigeon(chat_id).name or texts.PIGEON_NAME


def candidates(store: Store, rng: random.Random, pool: Sequence[str], chat_id: int) -> list[str]:
    used = store.past_names(chat_id) | {current_name(store, chat_id)}
    free = [name for name in pool if name not in used]
    return rng.sample(free, min(OPTIONS, len(free)))


async def start(
    store: Store,
    clock: Clock,
    rng: random.Random,
    sender: PollSender,
    pool: Sequence[str],
    chat_id: int,
) -> Start:
    """Open a vote and post its poll. A poll that fails to send leaves no vote behind.

    If only the response was lost, a retry posts a second poll. That is rare and costs one
    stray poll. Keeping the vote instead would lose the month to any failed connection.
    """
    if store.open_name_vote(chat_id) is not None:
        return Start.ALREADY
    options = candidates(store, rng, pool, chat_id)
    if len(options) < MIN_OPTIONS:
        return Start.NO_OPTIONS
    now = clock.now()
    vote_id = store.create_name_vote(chat_id, options, now, now + VOTE_TIME)
    if vote_id is None:
        return Start.ALREADY
    try:
        message_id = await sender.send_poll(chat_id, texts.NAME_QUESTION, options)
    except Exception:
        store.delete_name_vote(chat_id, vote_id)
        raise
    store.set_name_vote_message(chat_id, vote_id, message_id)
    return Start.STARTED


def too_soon(store: Store, chat_id: int, now: datetime) -> bool:
    last = store.last_name_vote_at(chat_id)
    return last is not None and now - last < MIN_GAP


def pick_winner(rng: random.Random, options: Sequence[str], counts: Sequence[int]) -> str | None:
    """The most voted option, random among leaders. None if nobody voted."""
    top = max(counts, default=0)
    if top == 0:
        return None
    return rng.choice([name for name, count in zip(options, counts, strict=True) if count == top])


def close(
    store: Store,
    rng: random.Random,
    vote: NameVote,
    counts: Sequence[int] | None,
    now: datetime,
) -> None:
    """Apply the result. counts is None if the poll is gone, and then there is nothing to say."""
    winner = pick_winner(rng, vote.options, counts) if counts is not None else None
    old_name = current_name(store, vote.chat_id)
    store.close_name_vote(vote.chat_id, vote.id, winner, old_name, now, reported=counts is None)
    store.mark_dashboard_dirty(vote.chat_id)


def abandon(store: Store, chat_id: int, now: datetime) -> None:
    """Stop trying: end the vote that is due without a result, drop any pending announcement.

    A vote that is not due yet is healthy and stays open.
    """
    vote = store.open_name_vote(chat_id)
    if vote is not None and now >= vote.closes_at:
        store.close_name_vote(chat_id, vote.id, None, current_name(store, chat_id), now)
    closed = store.unreported_name_vote(chat_id)
    if closed is not None:
        store.mark_name_vote_reported(chat_id, closed.id)


def report(store: Store, vote: NameVote) -> str:
    if vote.winner is not None:
        return texts.NAME_RENAMED.format(name=vote.winner)
    return texts.NAME_NO_VOTES.format(name=current_name(store, vote.chat_id))
