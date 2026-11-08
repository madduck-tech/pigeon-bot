"""Wiring: builds the aiogram bot and dispatcher, runs long polling and the scheduler."""

from __future__ import annotations

import asyncio
import logging
import random

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from pigeon.clock import Clock, SystemClock
from pigeon.config import Settings
from pigeon.content import Content, load_content
from pigeon.handlers.pigeon import build_router
from pigeon.repo.store import Store
from pigeon.scheduler import arrivals, fortunes
from pigeon.telegram.commands import publish_menus
from pigeon.telegram.messages import TelegramArrivalSender

log = logging.getLogger("pigeon")

SCHEDULER_INTERVAL_SECONDS = 30


def build_bot(token: str) -> Bot:
    """Every message is HTML: render/ escapes what it inserts, and nothing else builds markup."""
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def _scheduler_pass(store, clock, rng, sender, content: Content) -> None:
    """One pass. A failure here is logged: the task must outlive it, or everything stops."""
    for step in (
        lambda: arrivals.tick(store, clock, rng, sender, content),
        lambda: fortunes.tick(
            store, clock, rng, sender, content.fortunes, content.names, content.quests
        ),
    ):
        try:
            await step()
        except Exception:
            log.exception("scheduler pass failed")


async def _scheduler_loop(
    store: Store, clock: Clock, bot: Bot, rng: random.Random, content: Content
) -> None:
    sender = TelegramArrivalSender(bot, store, clock, content.quests)
    while True:
        await _scheduler_pass(store, clock, rng, sender, content)
        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)


async def run_bot(settings: Settings) -> None:
    if settings.bot_token is None:
        raise ValueError("PIGEON_BOT_TOKEN is not set")
    content = load_content(settings.content_dir)
    store = Store(settings.db_path)
    bot = build_bot(settings.bot_token.get_secret_value())
    clock = SystemClock()
    dp = Dispatcher()
    rng = random.Random()
    dp.include_router(build_router(store, clock, content, rng))
    await publish_menus(bot)
    scheduler = asyncio.create_task(_scheduler_loop(store, clock, bot, rng, content))
    log.info("pigeon polling and scheduler started")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.cancel()
        store.close()
        await bot.session.close()
