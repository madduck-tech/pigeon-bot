"""Wiring: builds the aiogram bot and dispatcher, then runs long polling."""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher

from pigeon.clock import SystemClock
from pigeon.config import Settings
from pigeon.handlers.pigeon import build_router
from pigeon.repo.store import Store

log = logging.getLogger("pigeon")


async def run_bot(settings: Settings) -> None:
    if settings.bot_token is None:
        raise ValueError("PIGEON_BOT_TOKEN is not set")
    store = Store(settings.db_path)
    bot = Bot(token=settings.bot_token.get_secret_value())
    dp = Dispatcher()
    dp.include_router(build_router(store, SystemClock()))
    log.info("pigeon polling started")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        store.close()
        await bot.session.close()
