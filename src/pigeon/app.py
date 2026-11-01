"""Wiring: builds the aiogram bot and dispatcher, runs long polling and the scheduler."""

from __future__ import annotations

import asyncio
import logging
import random

from aiogram import Bot, Dispatcher

from pigeon.clock import Clock, SystemClock
from pigeon.config import Settings
from pigeon.handlers.pigeon import build_router
from pigeon.repo.store import Store
from pigeon.scheduler import arrivals
from pigeon.telegram.dashboard import refresh_dashboard
from pigeon.telegram.messages import TelegramArrivalSender

log = logging.getLogger("pigeon")

SCHEDULER_INTERVAL_SECONDS = 30


async def _scheduler_loop(store: Store, clock: Clock, bot: Bot, rng: random.Random) -> None:
    sender = TelegramArrivalSender(bot)

    async def refresh(chat_id: int) -> None:
        await refresh_dashboard(bot, store, clock, chat_id)

    while True:
        await arrivals.tick(store, clock, rng, sender, refresh)
        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)


async def run_bot(settings: Settings) -> None:
    if settings.bot_token is None:
        raise ValueError("PIGEON_BOT_TOKEN is not set")
    store = Store(settings.db_path)
    bot = Bot(token=settings.bot_token.get_secret_value())
    clock = SystemClock()
    dp = Dispatcher()
    dp.include_router(build_router(store, clock))
    scheduler = asyncio.create_task(_scheduler_loop(store, clock, bot, random.Random()))
    log.info("pigeon polling and scheduler started")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.cancel()
        store.close()
        await bot.session.close()
