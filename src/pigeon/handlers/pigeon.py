"""Telegram handlers for M1: /pigeon commands and the hit button. Thin: logic lives in services."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from pigeon.clock import Clock
from pigeon.render import texts
from pigeon.repo.store import Store
from pigeon.services import game

GROUP_TYPES = {"group", "supergroup"}
ADMIN_STATUSES = {"creator", "administrator"}


def _pluck_keyboard(event_id: int) -> InlineKeyboardMarkup:
    button = InlineKeyboardButton(text=texts.PLUCK_BUTTON, callback_data=f"pg:hit:{event_id}")
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def build_router(store: Store, clock: Clock) -> Router:
    router = Router(name="pigeon")

    async def is_admin(message: Message) -> bool:
        member = await message.bot.get_chat_member(message.chat.id, message.from_user.id)
        return member.status in ADMIN_STATUSES

    @router.message(Command("pigeon"))
    async def pigeon_command(message: Message, command: CommandObject) -> None:
        if message.chat.type not in GROUP_TYPES:
            await message.answer(texts.NOT_GROUP)
            return
        chat_id = message.chat.id
        arg = (command.args or "").strip().lower()

        if arg == "help":
            await message.answer(texts.HELP)
            return

        if arg == "setup":
            if not await is_admin(message):
                await message.answer(texts.ADMIN_ONLY)
                return
            store.enable_chat(chat_id, clock.now())
            await message.answer(texts.SETUP_DONE)
            return

        if not store.is_enabled(chat_id):
            await message.answer(texts.NOT_ENABLED)
            return

        if arg == "stats":
            rows = store.leaderboard(chat_id)
            await message.answer(texts.stats(rows) if rows else texts.NO_STATS)
            return

        if arg == "arrive":
            if not await is_admin(message):
                await message.answer(texts.ADMIN_ONLY)
                return
            event = game.arrive(store, clock, chat_id)
            if event is None:
                await message.answer(texts.ALREADY_SITTING)
                return
            sent = await message.answer(
                texts.ARRIVAL.format(count=0), reply_markup=_pluck_keyboard(event.id)
            )
            store.set_event_message(event.id, sent.message_id)
            return

        current = store.current_open_event(chat_id)
        if current is not None and clock.now() < current.closes_at:
            await message.answer(texts.STATUS_SITTING.format(count=store.hit_count(current.id)))
        else:
            await message.answer(texts.STATUS_AWAY)

    @router.callback_query(F.data.startswith("pg:hit:"))
    async def hit_callback(callback: CallbackQuery) -> None:
        if callback.message is None or callback.data is None:
            await callback.answer()
            return
        chat_id = callback.message.chat.id
        event_id = int(callback.data.rsplit(":", 1)[1])
        name = callback.from_user.full_name
        outcome = game.hit(store, clock, chat_id, event_id, callback.from_user.id, name)

        if outcome is game.HitOutcome.HIT:
            await callback.answer(texts.HIT_OK)
            await _refresh_arrival(callback, store, event_id)
        elif outcome is game.HitOutcome.ALREADY:
            await callback.answer(texts.HIT_ALREADY)
        elif outcome is game.HitOutcome.CLOSED:
            await callback.answer(texts.HIT_CLOSED)
            await _mark_flown(callback, store, event_id)
        else:
            await callback.answer(texts.HIT_NO_EVENT)

    return router


async def _refresh_arrival(callback: CallbackQuery, store: Store, event_id: int) -> None:
    message = callback.message
    if message is None:
        return
    count = store.hit_count(event_id)
    try:
        await message.edit_text(
            texts.ARRIVAL.format(count=count), reply_markup=_pluck_keyboard(event_id)
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


async def _mark_flown(callback: CallbackQuery, store: Store, event_id: int) -> None:
    message = callback.message
    if message is None:
        return
    count = store.hit_count(event_id)
    try:
        await message.edit_text(texts.ARRIVAL_FLEW.format(count=count), reply_markup=None)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise
