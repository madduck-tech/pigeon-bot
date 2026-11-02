"""Ends duels whose time ran out and updates their messages. Runs on every scheduler pass.

Only edits an existing message, so it posts nothing and quiet hours do not apply.
"""

from __future__ import annotations

import logging
from typing import Protocol

from pigeon.clock import Clock
from pigeon.repo.store import Store
from pigeon.services import duels

log = logging.getLogger("pigeon.duels")


class DuelEditor(Protocol):
    async def edit_text(self, chat_id: int, message_id: int, text: str) -> None: ...


async def tick(store: Store, clock: Clock, editor: DuelEditor) -> None:
    for duel in duels.sweep(store, clock):
        if duel.message_id is None:
            continue
        try:
            await editor.edit_text(duel.chat_id, duel.message_id, duels.ended_text(store, duel))
        except Exception:
            # The duel is already over. A stale message must not stop the other chats.
            log.exception("chat %s: could not update duel %s", duel.chat_id, duel.id)
