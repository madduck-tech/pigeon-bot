"""Telegram handlers: /pigeon commands and the hit button. Thin: logic lives in services."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from pigeon.clock import Clock
from pigeon.render import texts
from pigeon.repo.store import Store, minute_key
from pigeon.services import bets, darts, game
from pigeon.telegram.messages import TelegramArrivalSender, arrival_keyboard

GROUP_TYPES = {"group", "supergroup"}
ADMIN_STATUSES = {"creator", "administrator"}
DEFAULT_TIMEZONE = "UTC"


def _valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


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
        parts = (command.args or "").split()
        sub = parts[0].lower() if parts else ""

        if sub == "help":
            await message.answer(texts.HELP)
            return

        if sub == "setup":
            if not await is_admin(message):
                await message.answer(texts.ADMIN_ONLY)
                return
            tz = parts[1] if len(parts) > 1 else DEFAULT_TIMEZONE
            if not _valid_timezone(tz):
                await message.answer(texts.TZ_BAD)
                return
            store.enable_chat(chat_id, clock.now(), tz)
            await message.answer(texts.SETUP_DONE.format(tz=tz))
            return

        if not store.is_enabled(chat_id):
            await message.answer(texts.NOT_ENABLED)
            return

        if sub == "quiet":
            if not await is_admin(message):
                await message.answer(texts.ADMIN_ONLY)
                return
            bounds = _quiet_bounds(parts[1:3])
            if bounds is None:
                await message.answer(texts.QUIET_BAD)
                return
            store.set_quiet_hours(chat_id, *bounds)
            await message.answer(texts.QUIET_SET.format(start=bounds[0], end=bounds[1]))
            return

        if sub == "stats":
            rows = store.leaderboard(chat_id)
            await message.answer(texts.stats(rows) if rows else texts.NO_STATS)
            return

        if sub == "arrive":
            if not await is_admin(message):
                await message.answer(texts.ADMIN_ONLY)
                return
            event = game.arrive(store, clock, chat_id, source="manual")
            if event is None:
                await message.answer(texts.ALREADY_SITTING)
                return
            message_id = await TelegramArrivalSender(message.bot).send_arrival(chat_id, event)
            store.set_event_message(event.id, message_id)
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

    @router.callback_query(F.data.startswith("pg:dart:"))
    async def dart_callback(callback: CallbackQuery) -> None:
        if callback.message is None or callback.data is None:
            await callback.answer()
            return
        chat_id = callback.message.chat.id
        event_id = int(callback.data.rsplit(":", 1)[1])
        user_id = callback.from_user.id
        outcome = darts.check(store, clock, chat_id, event_id, user_id)
        if outcome is darts.DartOutcome.ALREADY_TODAY:
            await callback.answer(texts.DART_ALREADY)
            return
        if outcome is darts.DartOutcome.CLOSED:
            await callback.answer(texts.DART_CLOSED)
            return
        if outcome is darts.DartOutcome.NO_EVENT:
            await callback.answer(texts.HIT_NO_EVENT)
            return
        await callback.answer()
        throw = await callback.message.answer_dice(emoji="🎯")
        value = throw.dice.value
        points = darts.record(
            store, clock, chat_id, event_id, user_id, callback.from_user.full_name, value
        )
        if points is None:
            await throw.reply(texts.DART_ALREADY)
            return
        await throw.reply(texts.DART_RESULT.format(value=value, points=points))

    @router.callback_query(F.data.startswith("pg:bet:"))
    async def bet_callback(callback: CallbackQuery) -> None:
        if callback.message is None or callback.data is None:
            await callback.answer()
            return
        chat_id = callback.message.chat.id
        _, _, event_part, choice = callback.data.split(":")
        outcome = bets.place(
            store,
            clock,
            chat_id,
            int(event_part),
            callback.from_user.id,
            callback.from_user.full_name,
            choice,
        )
        replies = {
            bets.BetOutcome.PLACED: texts.BET_PLACED.format(choice=texts.BET_CHOICE[choice]),
            bets.BetOutcome.ALREADY: texts.BET_ALREADY,
            bets.BetOutcome.CLOSED: texts.BET_CLOSED,
            bets.BetOutcome.NO_BALANCE: texts.BET_NO_BALANCE,
            bets.BetOutcome.NO_EVENT: texts.HIT_NO_EVENT,
        }
        await callback.answer(replies[outcome])

    @router.message(F.chat.type.in_(GROUP_TYPES))
    async def count_group_message(message: Message) -> None:
        # Live-chat check: only a per-minute count is kept, never the message text.
        # Runs after the /pigeon command handler, so commands are not counted.
        if store.is_enabled(message.chat.id):
            store.record_message(message.chat.id, minute_key(clock.now()))

    return router


def _quiet_bounds(args: list[str]) -> tuple[int, int] | None:
    if len(args) != 2 or not all(a.isdigit() for a in args):
        return None
    start, end = int(args[0]), int(args[1])
    if not (0 <= start <= 23 and 0 <= end <= 23 and end < start):
        return None
    return start, end


async def _refresh_arrival(callback: CallbackQuery, store: Store, event_id: int) -> None:
    message = callback.message
    if message is None:
        return
    count = store.hit_count(event_id)
    try:
        await message.edit_text(
            texts.ARRIVAL.format(count=count), reply_markup=arrival_keyboard(event_id)
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
