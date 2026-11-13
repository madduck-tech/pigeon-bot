"""Manual game suppresses a pending automatic arrival through restart, with real Telegram."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from pigeon.clock import FixedClock
from pigeon.content import load_content
from pigeon.repo.store import Store
from pigeon.scheduler import arrivals
from pigeon.services import game, shoo
from pigeon.telegram.messages import TelegramArrivalSender
from tools.stand.harness import REPO, Stand

DESCRIPTION = "delivered manual game, shoo, pending plan, SQLite restart, next-day auto sentinel"


class Early(random.Random):
    def random(self):
        return 0.0

    def randrange(self, *args):
        return 0


async def run(stand: Stand) -> None:
    content = load_content(REPO / "content")
    store, chat = stand.store, stand.config.chat_id
    base = datetime(2026, 11, 2, 5, tzinfo=UTC)  # Moscow 08:00, before quiet hours end
    clock, rng = FixedClock(base), Early(29)
    store.enable_chat(chat, base, "Europe/Moscow")
    sender = TelegramArrivalSender(stand.bot, store, clock, content.quests)
    step = await stand.run(
        "arrivals: create pending daily plan before manual game",
        lambda: arrivals.tick(store, clock, rng, sender, content),
    )
    plan = store.get_plan(chat, "2026-11-02")
    step.check("pending plan persisted", plan is not None and plan.status == "pending")
    step.expect_no_call("sendMessage")
    clock.set(base + timedelta(minutes=59))
    event = game.arrive(store, clock, chat)

    async def deliver_manual():
        message_id = await sender.send_arrival(chat, event)
        store.set_event_message(event.id, message_id)

    clock.set(plan.planned_at)
    step = await stand.run(
        "arrivals adverse: due tick while manual delivery is still unconfirmed",
        lambda: arrivals.tick(store, clock, rng, sender, content),
    )
    step.check("unconfirmed event sentinel", store.get_event(event.id).message_id is None)
    step.check("plan remains retryable", store.get_plan(chat, "2026-11-02").status == "pending")
    step.expect_no_call("sendMessage")
    step = await stand.run("arrivals: deliver manual game through Telegram", deliver_manual)
    step.check("one accepted manual send", len(step.sent()) == 1)
    step.check("manual delivery persisted", store.get_event(event.id).message_id is not None)
    assert (
        shoo.shoo(store, clock, rng, content.shoos, chat, event.id, stand.config.admin_id, "Админ")
        is shoo.ShooOutcome.FLEW
    )

    async def mark_closed():
        closed = store.get_event(event.id)
        await sender.mark_flown(
            chat, closed.message_id, game.flown_text(store, closed, clock.now(), content.quests)
        )

    step = await stand.run("arrivals: close delivered manual game", mark_closed)
    step.check("manual closed before due plan", store.get_event(event.id).status == "closed")
    store.close()
    store = Store(stand.db_path)
    stand.store = store  # dispatcher is unused after reopen; Stand.close owns this connection
    sender = TelegramArrivalSender(stand.bot, store, clock, content.quests)
    clock.set(plan.planned_at)
    for n in range(2):
        step = await stand.run(
            f"arrivals adverse: due pending plan after restart, tick {n + 1}",
            lambda: arrivals.tick(store, clock, rng, sender, content),
        )
        retained = store.get_event(event.id)
        step.check(
            "delivered closed manual sentinel survives restart",
            retained.source == "manual"
            and retained.status == "closed"
            and retained.message_id is not None,
        )
        step.expect_no_call("sendMessage")
        step.check("day skipped in SQLite", store.get_plan(chat, "2026-11-02").status == "skipped")
        step.check("no weekly auto quota consumed", store.count_auto_events_since(chat, base) == 0)

    clock.set(plan.planned_at + timedelta(days=1))
    step = await stand.run(
        "arrivals: next local day positive automatic send sentinel",
        lambda: arrivals.tick(store, clock, rng, sender, content),
    )
    step.check("one accepted automatic send next day", len(step.sent()) == 1)
    auto = store.current_open_event(chat)
    step.check("automatic event persisted", auto is not None and auto.source == "auto")
    step.check("next day fired", store.get_plan(chat, "2026-11-03").status == "fired")
    if auto is not None:
        clock.set(auto.closes_at)
        step = await stand.run(
            "arrivals: cleanup next-day game through production expiry",
            lambda: arrivals.tick(store, clock, rng, sender, content),
        )
        step.check("automatic event closed", store.get_event(auto.id).status == "closed")
        step.check(
            "manual day remains skipped", store.get_plan(chat, "2026-11-02").status == "skipped"
        )
