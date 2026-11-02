"""Wiring: builds the aiogram bot and dispatcher, runs long polling and the scheduler."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Sequence

from aiogram import Bot, Dispatcher

from pigeon.clock import Clock, SystemClock
from pigeon.config import Settings
from pigeon.content import load_content
from pigeon.handlers.pigeon import build_router
from pigeon.repo.store import Store
from pigeon.scheduler import arrivals, duel_sweep, jobs
from pigeon.telegram.dashboard import refresh_dashboard
from pigeon.telegram.messages import TelegramArrivalSender

log = logging.getLogger("pigeon")

SCHEDULER_INTERVAL_SECONDS = 30


async def _scheduler_pass(store, clock, rng, sender, refresh, names: Sequence[str] = ()) -> None:
    """One pass. A failure here is logged: the task must outlive it, or arrivals stop for good."""
    for step in (
        lambda: jobs.tick(store, clock, sender, rng, names),
        lambda: duel_sweep.tick(store, clock, sender),
        lambda: arrivals.tick(store, clock, rng, sender, refresh),
    ):
        try:
            await step()
        except Exception:
            log.exception("scheduler pass failed")


async def _scheduler_loop(
    store: Store, clock: Clock, bot: Bot, rng: random.Random, names: Sequence[str]
) -> None:
    sender = TelegramArrivalSender(bot)

    async def refresh(chat_id: int) -> None:
        await refresh_dashboard(bot, store, clock, chat_id)

    while True:
        await _scheduler_pass(store, clock, rng, sender, refresh, names)
        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)


async def run_bot(settings: Settings) -> None:
    if settings.bot_token is None:
        raise ValueError("PIGEON_BOT_TOKEN is not set")
    content = load_content(settings.content_dir)
    store = Store(settings.db_path)
    bot = Bot(token=settings.bot_token.get_secret_value())
    clock = SystemClock()
    dp = Dispatcher()
    rng = random.Random()
    dp.include_router(build_router(store, clock, content, rng))
    scheduler = asyncio.create_task(_scheduler_loop(store, clock, bot, rng, content.names))
    log.info("pigeon polling and scheduler started")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.cancel()
        store.close()
        await bot.session.close()
