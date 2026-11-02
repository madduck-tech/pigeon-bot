"""The daily fortune: one post per chat per local day, once the morning has come.

Runs apart from arrivals, so a failed post cannot cost a chat its pigeon, or the other way round.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Sequence
from typing import Protocol

from aiogram.exceptions import TelegramMigrateToChat

from pigeon.budget import limits
from pigeon.clock import Clock
from pigeon.render import texts
from pigeon.repo.store import ChatSettings, Store
from pigeon.services import fortune

log = logging.getLogger("pigeon.fortunes")


class FortuneSender(Protocol):
    async def send_text(self, chat_id: int, text: str) -> None: ...


async def _post(
    store: Store,
    clock: Clock,
    rng: random.Random,
    sender: FortuneSender,
    lines: Sequence[str],
    chat: ChatSettings,
) -> None:
    now = clock.now()
    if not fortune.daily_due(chat, now):
        return
    local_day = limits.chat_local(chat, now).date().isoformat()
    if not store.claim_daily_fortune(chat.chat_id, local_day):
        return
    try:
        await sender.send_text(chat.chat_id, texts.daily_fortune(rng.choice(lines)))
    except Exception:
        # Nobody saw it, so the next tick tries again. If only the response was lost, the chat
        # gets a second post, which is cheaper than losing the day to one failed connection.
        store.release_daily_fortune(chat.chat_id, local_day)
        raise


async def tick(
    store: Store,
    clock: Clock,
    rng: random.Random,
    sender: FortuneSender,
    lines: Sequence[str],
) -> None:
    """Run one pass over every enabled chat. A failure in one chat does not stop the others."""
    for chat in store.enabled_chats():
        try:
            await _post(store, clock, rng, sender, lines, chat)
        except TelegramMigrateToChat as exc:
            log.warning("chat %s moved to %s", chat.chat_id, exc.migrate_to_chat_id)
            store.migrate_chat(chat.chat_id, exc.migrate_to_chat_id)
        except Exception:
            log.exception("chat %s: daily fortune failed", chat.chat_id)
