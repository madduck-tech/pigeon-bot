"""The Monday summary: the former daily slot is now weekly only.

Runs apart from arrivals, so a failed post cannot cost a chat its pigeon, or the other way round.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Sequence
from typing import Protocol

from aiogram.exceptions import TelegramForbiddenError, TelegramMigrateToChat

from pigeon.budget import limits
from pigeon.clock import Clock
from pigeon.content import Fortune, Quest
from pigeon.repo.store import ChatSettings, Store
from pigeon.services import fortune, weekly

log = logging.getLogger("pigeon.fortunes")


class FortuneSender(Protocol):
    async def send_text(self, chat_id: int, text: str) -> None: ...


async def _post(
    store: Store,
    clock: Clock,
    rng: random.Random,
    sender: FortuneSender,
    lines: Sequence[Fortune],
    names: Sequence[str],
    chat: ChatSettings,
    quests: tuple[Quest, ...],
) -> None:
    now = clock.now()
    weekly.ensure(store, chat.chat_id, now, quests, rng)
    if (
        not quests
        or limits.chat_local(chat, now).weekday() != 0
        or not fortune.daily_due(chat, now)
    ):
        return
    local_day = limits.chat_local(chat, now).date().isoformat()
    with store.transaction():
        issue = store.get_fortune_issue(chat.chat_id, 0, local_day)
        if issue:
            rendered = issue.rendered_text if issue.sent_at is None else None
        else:
            rendered = weekly.monday_text(store, chat.chat_id, now, quests)
            store.save_fortune_issue(chat.chat_id, 0, local_day, None, rendered)
    if rendered is None:
        return
    await sender.send_text(chat.chat_id, rendered)
    store.mark_daily_fortune_sent(chat.chat_id, local_day, clock.now())


async def tick(
    store: Store,
    clock: Clock,
    rng: random.Random,
    sender: FortuneSender,
    lines: Sequence[Fortune],
    names: Sequence[str] = ("Гена",),
    quests: tuple[Quest, ...] = (),
) -> None:
    """Run one pass over every enabled chat. A failure in one chat does not stop the others."""
    for chat in store.enabled_chats():
        try:
            await _post(store, clock, rng, sender, lines, names, chat, quests)
        except TelegramForbiddenError:
            store.disable_chat(chat.chat_id)
            log.warning("chat %s: Telegram access forbidden; chat disabled", chat.chat_id)
        except TelegramMigrateToChat as exc:
            log.warning("chat %s moved to %s", chat.chat_id, exc.migrate_to_chat_id)
            store.migrate_chat(chat.chat_id, exc.migrate_to_chat_id)
        except Exception:
            log.exception("chat %s: daily fortune failed", chat.chat_id)
