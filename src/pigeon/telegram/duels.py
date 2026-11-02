"""Runs a duel bout in the chat: the dart throws and the result message."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from aiogram import Bot
from aiogram.types import ReplyParameters

from pigeon.clock import Clock
from pigeon.render import texts
from pigeon.repo.store import Duel, Store
from pigeon.services import duels

Sleep = Callable[[float], Awaitable[None]]
AwardNames = Callable[[list[str]], Sequence[str]]

# Telegram allows 20 messages a minute in a group. This pace alone would allow 30; what keeps
# a bout under the limit is duels.MAX_ROUNDS, at most 18 throws.
THROW_PAUSE_SECONDS = 2.0
# Long enough for the last dart to land, so the result does not spoil it.
LANDING_PAUSE_SECONDS = 4.0


async def run_bout(
    bot: Bot, store: Store, clock: Clock, duel: Duel, sleep: Sleep, award_names: AwardNames
) -> None:
    """Throw until the bout is decided. An error leaves it to expire: see services/duels.py."""
    reply_to = None
    if duel.message_id is not None:
        reply_to = ReplyParameters(message_id=duel.message_id, allow_sending_without_reply=True)

    async def throw() -> int:
        sent = await bot.send_dice(duel.chat_id, emoji="🎯", reply_parameters=reply_to)
        await sleep(THROW_PAUSE_SECONDS)
        return sent.dice.value

    rounds: list[duels.Round] = []
    while not duels.bout_over(rounds):
        current = store.get_duel(duel.chat_id, duel.id)
        if current is None or current.state != "active":
            return  # the sweep ended it, and the chat has already been told
        rounds.append((await throw(), await throw()))
    await sleep(LANDING_PAUSE_SECONDS)

    earned = duels.finish(store, clock, duel, rounds)
    if earned is None:
        return
    winner = duels.winner_of(duel, rounds)
    result = texts.duel_result(
        duels.member_name(store, duel.chat_id, duel.challenger_id),
        duels.member_name(store, duel.chat_id, duel.opponent_id),
        rounds,
        duels.score(rounds),
        duels.member_name(store, duel.chat_id, winner) if winner is not None else None,
        duels.WIN_POINTS,
    )
    await bot.send_message(
        duel.chat_id,
        texts.with_awards(result, list(award_names(earned))),
        reply_parameters=reply_to,
    )
