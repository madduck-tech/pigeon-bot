"""The pinned dashboard: one message per chat with the pigeon's status, trophy and weekly top.

Refreshed by the scheduler, at most once per tick and only when the chat is marked dirty.
Creating and pinning needs the bot to have the pin-messages right; without it the dashboard
still works as an unpinned message.
"""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from pigeon.budget import limits
from pigeon.clock import Clock
from pigeon.render import texts
from pigeon.repo.store import Store

log = logging.getLogger("pigeon.dashboard")

TOP_SIZE = 3


def build_text(store: Store, clock: Clock, chat_id: int) -> str:
    chat = store.get_chat(chat_id)
    assert chat is not None
    now = clock.now()
    local_now = now.astimezone(ZoneInfo(chat.timezone))
    current = store.current_open_event(chat_id)
    sitting = current is not None and now < current.closes_at
    trophy = store.get_trophy(chat_id)
    holder = trophy.holder_name if trophy else None
    holder_days = (now - trophy.since).days if trophy else 0
    top = store.leaderboard(chat_id, limit=TOP_SIZE, since=limits.local_week_start_utc(local_now))
    return texts.dashboard(
        arrivals=store.arrivals_total(chat_id),
        sitting=sitting,
        holder=holder,
        holder_days=holder_days,
        top=top,
    )


async def refresh_dashboard(bot: Bot, store: Store, clock: Clock, chat_id: int) -> None:
    chat = store.get_chat(chat_id)
    if chat is None:
        return
    text = build_text(store, clock, chat_id)
    message_id = chat.dashboard_message_id
    if message_id is not None:
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
        except TelegramBadRequest as exc:
            reason = str(exc)
            if "message is not modified" in reason:
                pass
            elif "message to edit not found" in reason or "message can't be edited" in reason:
                message_id = None
            else:
                raise
    if message_id is None:
        sent = await bot.send_message(chat_id, text)
        store.set_dashboard_message(chat_id, sent.message_id)
        try:
            await bot.pin_chat_message(chat_id, sent.message_id, disable_notification=True)
        except TelegramBadRequest:
            log.warning("chat %s: could not pin the dashboard (missing right?)", chat_id)
    store.clear_dashboard_dirty(chat_id)
