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
from pigeon.llm.opencode import OpenCodeClient
from pigeon.repo.store import Store
from pigeon.scheduler import arrivals, chronicle, courier, fortunes
from pigeon.services.chronicle import ChronicleModel
from pigeon.services.fortune_decks import migrate_courier
from pigeon.services.usage import Usage
from pigeon.telegram.commands import publish_menus
from pigeon.telegram.messages import TelegramArrivalSender
from pigeon.telegram.usage import install_delivery_usage

log = logging.getLogger("pigeon")

SCHEDULER_INTERVAL_SECONDS = 30


def build_bot(token: str) -> Bot:
    """Every message is HTML: render/ escapes what it inserts, and nothing else builds markup."""
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def _scheduler_pass(store, clock, rng, sender, content: Content) -> None:
    """One pass. A failure here is logged: the task must outlive it, or everything stops."""
    for step in (
        lambda: arrivals.tick(store, clock, rng, sender, content),
        lambda: courier.tick(store, clock, rng, sender, content.fortunes, content.names),
        lambda: fortunes.tick(
            store, clock, rng, sender, content.fortunes, content.names, content.quests
        ),
    ):
        try:
            await step()
        except Exception:
            log.exception("scheduler pass failed")


async def _scheduler_loop(
    store: Store,
    clock: Clock,
    bot: Bot,
    rng: random.Random,
    content: Content,
    usage: Usage | None = None,
) -> None:
    sender = TelegramArrivalSender(bot, store, clock, content.quests)
    while True:
        if usage is not None:
            usage.maintain()
        await _scheduler_pass(store, clock, rng, sender, content)
        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)


async def _chronicle_loop(
    store: Store, clock: Clock, model: ChronicleModel, content: Content
) -> None:
    """Its own loop: waiting for the model must not hold up arrivals or posts (ADR-0014)."""
    while True:
        try:
            await chronicle.tick(store, clock, model, content.chronicle, content.quests)
        except Exception:
            log.exception("chronicle pass failed")
        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)


def chronicle_model(settings: Settings) -> OpenCodeClient | None:
    """The chronicle client, or None when no key is set and the chronicle is off."""
    key = settings.chronicle_api_key
    if key is None or not key.get_secret_value().strip():
        return None
    return OpenCodeClient(
        key.get_secret_value().strip(), settings.chronicle_model, settings.chronicle_base_url
    )


async def run_bot(settings: Settings) -> None:
    if settings.bot_token is None:
        raise ValueError("PIGEON_BOT_TOKEN is not set")
    content = load_content(settings.content_dir)
    store = Store(settings.db_path)
    migrate_courier(store, content.fortunes)
    bot = build_bot(settings.bot_token.get_secret_value())
    clock = SystemClock()
    usage = Usage(store.usage, clock)
    install_delivery_usage(bot, usage)
    dp = Dispatcher()
    rng = random.Random()
    model = chronicle_model(settings)
    dp.include_router(
        build_router(store, clock, content, rng, chronicle_enabled=model is not None, usage=usage)
    )
    await publish_menus(bot)
    tasks = [asyncio.create_task(_scheduler_loop(store, clock, bot, rng, content, usage))]
    if model is not None and content.chronicle is not None:
        tasks.append(asyncio.create_task(_chronicle_loop(store, clock, model, content)))
        log.info("weekly chronicle on, model %s", settings.chronicle_model)
    log.info("pigeon polling and scheduler started")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for task in tasks:
            task.cancel()
        store.close()
        await bot.session.close()
