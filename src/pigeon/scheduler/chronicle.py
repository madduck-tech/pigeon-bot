"""The weekly chronicle pass (ADR-0014). It runs in its own loop, so a slow model never delays
arrivals or posts."""

from __future__ import annotations

import logging

from pigeon.clock import Clock
from pigeon.content import ChroniclePrompt, Quest
from pigeon.repo.store import Store
from pigeon.services import chronicle
from pigeon.services.chronicle import ChronicleModel

log = logging.getLogger("pigeon.chronicle")


async def tick(
    store: Store,
    clock: Clock,
    model: ChronicleModel,
    prompt: ChroniclePrompt,
    quests: tuple[Quest, ...],
) -> None:
    """One pass over every enabled chat. A failure in one chat does not stop the others."""
    for chat in store.enabled_chats():
        try:
            await chronicle.prepare(store, clock, model, prompt, chat, quests)
        except Exception:
            log.exception("chat %s: chronicle pass failed", chat.chat_id)
