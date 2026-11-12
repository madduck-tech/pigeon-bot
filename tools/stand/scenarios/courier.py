"""Morning courier: real Telegram parsing, no keyboard/game mutation, adverse time windows."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from pigeon.clock import FixedClock
from pigeon.content import FortuneFormat, load_content
from pigeon.repo.store import Store
from pigeon.scheduler import courier
from pigeon.services import game
from pigeon.telegram.messages import TelegramArrivalSender
from tools.stand.harness import REPO, Stand

DESCRIPTION = (
    "five standalone formats sent through the production morning scheduler; escaped scene name, "
    "no keyboard, open game unchanged, repeat/reopen and 14:00/pause/no-window adverse cases"
)


class Morning(random.Random):
    def random(self):
        return 0.0

    def randrange(self, *args):
        return 0


async def run(stand: Stand) -> None:
    content = load_content(REPO / "content")
    store, chat = stand.store, stand.config.chat_id
    base = datetime(2026, 11, 2, 6, tzinfo=UTC)  # Monday, Moscow09:00
    store.enable_chat(chat, base, "Europe/Moscow")
    event = game.arrive(store, FixedClock(base), chat, rng=random.Random(3))
    deck = store.get_pigeon_deck(chat)
    formats = list(FortuneFormat)
    for n, fmt in enumerate(formats):
        at = base + timedelta(days=n if n < 4 else 7)
        clock = FixedClock(at)
        line = next(f for f in content.fortunes if f.format == fmt)
        sender = TelegramArrivalSender(stand.bot, store, clock, content.quests)

        async def post(clock=clock, sender=sender, line=line):
            await courier.tick(store, clock, Morning(1), sender, [line], ["<Гена> & друг"])

        step = await stand.run(f"courier: Telegram parses {fmt}", post)
        calls = step.sent()
        step.check("exactly one accepted sendMessage", len(calls) == 1, len(calls))
        if calls:
            call = calls[0]
            shown = (call.get("result") or {}).get("text", "")
            step.check("no keyboard", not call["params"].get("reply_markup"))
            step.check("HTML mode", call["params"].get("parse_mode") == "HTML")
            step.check("courier immediately departed", "сразу улетел" in shown, shown)
            entities = {e.get("type") for e in (call.get("result") or {}).get("entities", [])}
            step.check("no mentions or links", entities <= {"bold", "italic"}, sorted(entities))
            if fmt == FortuneFormat.SCENE:
                step.check("hostile-shaped name is plain text", "<Гена> & друг" in shown)
        step.check("game event unchanged", store.current_open_event(chat) == event)
        step.check("game deck unchanged", store.get_pigeon_deck(chat) == deck)
        step.check("publication persisted", store.courier_published(chat, at.date().isoformat()))
        step = await stand.run("courier: repeated scheduler tick", post)
        step.expect_no_call("sendMessage")

    # Reopen the persisted production state: no new day/plan or duplicate publication.
    reopened = Store(stand.run_dir / "reopen.db")
    # Use SQLite backup to retain all state, rather than manufacture a fired plan.
    store._conn.backup(reopened._conn)
    try:
        clock = FixedClock(base + timedelta(days=7))
        sender = TelegramArrivalSender(stand.bot, reopened, clock, content.quests)
        step = await stand.run(
            "courier: restart same published day",
            lambda: courier.tick(
                reopened, clock, Morning(1), sender, content.fortunes, content.names
            ),
        )
        step.expect_no_call("sendMessage")
        step.check(
            "published sentinel survives reopen", reopened.courier_published(chat, "2026-11-09")
        )
    finally:
        reopened.close()

    for label, day, hour, pause, quiet_end in [
        ("exactly14", 8, 14, False, 9),
        ("paused", 9, 9, True, 9),
        ("no morning window", 10, 13, False, 15),
    ]:
        clock = FixedClock(base + timedelta(days=day, hours=hour - 9))
        store.set_quiet_hours(chat, 23, quiet_end)
        store.set_paused_until(chat, clock.now() + timedelta(hours=1) if pause else None)
        sender = TelegramArrivalSender(stand.bot, store, clock, content.quests)
        step = await stand.run(
            f"courier adverse: {label}",
            lambda clock=clock, sender=sender: courier.tick(
                store, clock, Morning(1), sender, content.fortunes, content.names
            ),
        )
        step.check("adverse settings reached", store.get_chat(chat).quiet_end == quiet_end)
        step.expect_no_call("sendMessage")
        plan = store.courier_plan(chat, clock.now().date().isoformat())
        step.check("day skipped in persisted state", plan is not None and plan.status == "skipped")
    store.set_paused_until(chat, None)
    store.set_quiet_hours(chat, 23, 9)
