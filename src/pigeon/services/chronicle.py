"""Weekly chronicle: an LLM writes 2–3 sentences about the past week of one chat (ADR-0014).

The chronicle is prepared ahead of the Monday post, stored, and read when the post is built. The
post never waits for the model. The model client is injected; this module knows no HTTP library.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, timedelta
from typing import Protocol

from pigeon.budget import limits
from pigeon.clock import Clock
from pigeon.content import ChroniclePrompt, Quest
from pigeon.render import chronicle as render
from pigeon.repo.store import ChatSettings, ChronicleWeek, Store
from pigeon.services import weekly

log = logging.getLogger("pigeon.chronicle")

# The client has its own deadline; this one also bounds a client that ignores it.
TIMEOUT_SECONDS = 20
MAX_PLAYERS = 10

PIGEON_TYPES = {
    "normal": "обычный",
    "capped": "в кепке",
    "sleepy": "сонный",
    "golden": "золотой",
    "adult": "голубка с подарком",
}


class ChronicleModel(Protocol):
    async def complete(self, system: str, user: str, *, session_id: str) -> str: ...


def facts(
    store: Store, chat: ChatSettings, week_key: str, quests: tuple[Quest, ...], week: ChronicleWeek
) -> dict:
    """What goes to the model: metadata of the past week of this chat only. Names are data."""
    total = sum(week.arrivals_by_type.values())
    previous = (date.fromisoformat(week_key) - timedelta(days=7)).isoformat()
    quest_row = store.get_quest(chat.chat_id, previous)
    quest = next((q for q in quests if quest_row and q.id == quest_row.quest_id), None)
    trophy = store.get_trophy(chat.chat_id)
    return {
        "arrivals": {
            "total": total,
            "by_type": {
                PIGEON_TYPES.get(kind, kind): count for kind, count in week.arrivals_by_type.items()
            },
            "scared_away": week.scared_away,
            "flew_away_alone": max(total - week.scared_away, 0),
        },
        "players": [
            {
                "name": render.clean_name(player.name),
                "points": player.points,
                "hits": player.hits,
                "misses": player.misses,
                "big_hits": player.big_hits,
                "shoo_attempts": player.shoo_attempts,
                "scared_away": player.scared_away,
            }
            for player in week.players[:MAX_PLAYERS]
        ],
        "quest": (
            {"name": quest.name, "completed": quest_row.status == "completed"} if quest else None
        ),
        "golden_spit_holder": render.clean_name(trophy.holder_name) if trophy else None,
    }


def user_message(prompt: ChroniclePrompt, data: dict) -> str:
    return f"{prompt.instruction}\n{json.dumps(data, ensure_ascii=False, indent=1)}"


async def prepare(
    store: Store,
    clock: Clock,
    model: ChronicleModel,
    prompt: ChroniclePrompt,
    chat: ChatSettings,
    quests: tuple[Quest, ...],
) -> str | None:
    """Prepare this week's chronicle if it is due. Returns the stored status, or None if not due.

    Due on the chat's local Monday, while the Monday post is not prepared yet, once per week.
    Pause and quiet hours do not matter: nothing is published here.
    """
    now = clock.now()
    local = limits.chat_local(chat, now)
    if not quests or local.weekday() != 0:
        return None
    if store.get_fortune_issue(chat.chat_id, 0, local.date().isoformat()) is not None:
        return None  # the post is already built; a chronicle now would never be shown
    week_key, start, _ = weekly.bounds(now, chat.timezone)
    if store.get_chronicle(chat.chat_id, week_key) is not None:
        return None
    _, prev_start, _ = weekly.bounds(start - timedelta(days=1), chat.timezone)
    week = store.chronicle_week(chat.chat_id, prev_start, start)
    if not week.arrivals_by_type:
        store.claim_chronicle(chat.chat_id, week_key, "empty", now)
        return "empty"
    data = facts(store, chat, week_key, quests, week)
    # The row exists before the request, so a restart never asks the model twice for one week.
    if not store.claim_chronicle(chat.chat_id, week_key, "pending", now):
        return None
    text = None
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            raw = await model.complete(
                prompt.system,
                user_message(prompt, data),
                session_id=f"pigeon-chronicle-{chat.chat_id}-{week_key}",
            )
        text = render.clean(raw)
        if text is None:
            log.warning("chat %s: chronicle reply is empty or too long", chat.chat_id)
    except Exception as exc:  # the chronicle is optional; the post goes out without it
        log.warning("chat %s: chronicle unavailable (%s)", chat.chat_id, type(exc).__name__)
    store.finish_chronicle(chat.chat_id, week_key, text)
    return "ready" if text else "failed"
