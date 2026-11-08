"""Telegram handlers: /pigeon commands and the arrival buttons. Thin: logic lives in services."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from pigeon.budget import limits
from pigeon.clock import Clock
from pigeon.content import Content
from pigeon.render import texts
from pigeon.repo.store import Store
from pigeon.services import achievements, adult, fortune, game, weekly
from pigeon.services.oboobs import OboobsClient
from pigeon.services.shoo import ShooOutcome, shoo
from pigeon.telegram.messages import (
    TelegramArrivalSender,
    arrival_keyboard,
    gift_keyboard,
    timezone_keyboard,
)

log = logging.getLogger("pigeon.handlers")

GROUP_TYPES = {"group", "supergroup"}
ADMIN_STATUSES = {"creator", "administrator"}
PAUSE_DURATIONS = {"24h": timedelta(hours=24), "7d": timedelta(days=7)}
EVENT_ID = re.compile(r"[0-9]{1,19}")
MAX_EVENT_ID = 2**63 - 1  # SQLite INTEGER


def _valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        # OSError: a tzdata folder such as "Europe" opens as a directory on Python 3.12.
        return False
    return True


def build_router(
    store: Store,
    clock: Clock,
    content: Content,
    rng: random.Random,
    gift_provider=None,
    chronicle_enabled: bool = False,
) -> Router:
    router = Router(name="pigeon")
    gift_provider = gift_provider or OboobsClient()

    def award_names(codes: list[str]) -> list[str]:
        # Achievements of closed mechanics stay in old databases but have no text any more.
        return [content.achievements[code].name for code in codes if code in content.achievements]

    async def is_admin(message: Message) -> bool:
        # An anonymous admin posts as the chat itself, from a shared service account.
        if message.sender_chat is not None and message.sender_chat.id == message.chat.id:
            return True
        return await _is_chat_admin(message.bot, message.chat.id, message.from_user.id)

    @router.message(Command("pigeon"))
    async def pigeon_command(message: Message, command: CommandObject) -> None:
        if message.chat.type not in GROUP_TYPES:
            await message.answer(texts.NOT_GROUP)
            return
        chat_id = message.chat.id
        parts = (command.args or "").split()
        sub = parts[0].lower() if parts else ""

        if sub == "help":
            await message.answer(texts.help_text(chronicle_enabled))
            return

        if sub == "setup":
            if not await is_admin(message):
                await message.answer(texts.ADMIN_ONLY)
                return
            if len(parts) < 2:
                current = store.get_chat(chat_id) if store.is_enabled(chat_id) else None
                await message.answer(
                    texts.SETUP_CHOOSE_CURRENT.format(tz=current.timezone)
                    if current
                    else texts.SETUP_CHOOSE,
                    reply_markup=timezone_keyboard(content.timezones),
                )
                return
            tz = parts[1]
            if not _valid_timezone(tz):
                await message.answer(texts.TZ_BAD)
                return
            store.enable_chat(chat_id, clock.now(), tz)
            await message.answer(texts.SETUP_DONE.format(tz=tz))
            return

        if not store.is_enabled(chat_id):
            await message.answer(texts.NOT_ENABLED)
            return

        if sub == "adult":
            if not await is_admin(message):
                await message.answer(texts.ADMIN_ONLY)
                return
            if len(parts) != 2 or parts[1].lower() not in {"on", "off"}:
                await message.answer(texts.ADULT_BAD)
                return
            enabled = parts[1].lower() == "on"
            store.set_adult_enabled(chat_id, enabled)
            await message.answer(texts.ADULT_ON if enabled else texts.ADULT_OFF)
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

        if sub == "pause":
            if not await is_admin(message):
                await message.answer(texts.ADMIN_ONLY)
                return
            await message.answer(_apply_pause(store, clock, chat_id, parts[1:]))
            return

        if sub == "profile":
            user_id = message.from_user.id
            await message.answer(
                texts.profile(
                    message.from_user.full_name,
                    store.balance(chat_id, user_id),
                    award_names(store.achievements_of(chat_id, user_id)),
                    store.trophy_held(chat_id, user_id, clock.now()).days,
                    store.week_balance(
                        chat_id,
                        user_id,
                        *weekly.bounds(clock.now(), store.get_chat(chat_id).timezone)[1:],
                    ),
                )
            )
            return

        if sub == "fortune":
            line = fortune.draw(
                store,
                clock,
                rng,
                content.fortunes,
                store.get_chat(chat_id),
                message.from_user.id,
                content.names,
            )
            await message.reply(texts.FORTUNE_ALREADY if line is None else line)
            return

        if sub == "stats":
            if len(parts) > 1 and parts[1].lower() == "all":
                trophy = store.get_trophy(chat_id)
                result = texts.stats(
                    store.leaderboard(chat_id),
                    trophy.holder_name if trophy else None,
                    (clock.now() - trophy.since).days if trophy else 0,
                )
            else:
                _, start, end = weekly.bounds(clock.now(), store.get_chat(chat_id).timezone)
                result = texts.week_stats(
                    store.week_leaderboard(chat_id, start, end),
                    weekly.label(weekly.current(store, chat_id, clock.now(), content.quests)),
                    weekly.last_title(store, chat_id),
                )
            await message.answer(result)
            return

        if sub == "arrive":
            if not await is_admin(message):
                await message.answer(texts.ADMIN_ONLY)
                return
            event = game.arrive(
                store,
                clock,
                chat_id,
                game.pick_visitor(rng, content),
                rng=rng,
                dove_names=content.dove_names,
            )
            if event is None:
                await message.answer(texts.ALREADY_SITTING)
                return
            try:
                message_id = await TelegramArrivalSender(
                    message.bot, store, clock, content.quests
                ).send_arrival(chat_id, event)
            except Exception:
                game.delete_unsent_event(store, chat_id, event.id)
                raise
            store.set_event_message(event.id, message_id)
            return

        current = store.current_open_event(chat_id)
        if current is not None and clock.now() < current.closes_at:
            name = current.pigeon_name or texts.PIGEON_NAME
            await message.answer(
                texts.status_sitting(name, store.hit_count(current.id), current.pigeon_type)
            )
        else:
            await message.answer(texts.STATUS_AWAY)

    async def handle_hit(callback: CallbackQuery, event_id: int, area: str | None = None) -> None:
        if callback.message is None:
            await callback.answer()
            return
        chat_id = callback.message.chat.id
        outcome = game.hit(
            store,
            clock,
            chat_id,
            event_id,
            callback.from_user.id,
            callback.from_user.full_name,
            rng,
            content.hits,
            content.quests,
            area,
        )

        if outcome is game.HitOutcome.HIT:
            reaction, points = store.hit_result(chat_id, event_id, callback.from_user.id)
            earned = (
                achievements.after_hit(store, clock, chat_id, callback.from_user.id)
                if points
                else []
            )
            await callback.answer(texts.with_awards(f"{reaction} +{points}", award_names(earned)))
            await _refresh_arrival(callback, store, event_id, clock, content.quests)
        elif outcome is game.HitOutcome.ALREADY:
            await callback.answer(texts.HIT_ALREADY)
        elif outcome is game.HitOutcome.CLOSED:
            await callback.answer(texts.HIT_CLOSED)
            await _mark_flown(callback, store, event_id, clock, content.quests)
        elif outcome is game.HitOutcome.BAD_AREA:
            await callback.answer(texts.HIT_BAD_AREA)
        else:
            await callback.answer(texts.HIT_NO_EVENT)

    @router.callback_query(F.data.startswith("pg:hit:"))
    async def hit_callback(callback: CallbackQuery) -> None:
        event_id = _parse_event_id(callback.data, "hit")
        if event_id is None:
            await callback.answer(texts.HIT_NO_EVENT)
            return
        await handle_hit(callback, event_id)

    @router.callback_query(F.data.startswith("pg:aim:"))
    async def aim_callback(callback: CallbackQuery) -> None:
        parts = (callback.data or "").split(":")
        event_id = _event_id(parts[2]) if len(parts) == 4 else None
        if event_id is None:
            await callback.answer(texts.HIT_NO_EVENT)
            return
        if parts[3] not in adult.AREAS:
            await callback.answer(texts.HIT_BAD_AREA)
            return
        await handle_hit(callback, event_id, parts[3])

    @router.callback_query(F.data.startswith("pg:gift:"))
    async def gift_callback(callback: CallbackQuery) -> None:
        # Telegram accepts one answer per callback query, so every path answers exactly once.
        event_id = _parse_event_id(callback.data, "gift")
        if callback.message is None or event_id is None:
            await callback.answer(texts.HIT_NO_EVENT)
            return
        chat_id = callback.message.chat.id
        attempt_id = f"{rng.getrandbits(128):032x}"
        start = adult.begin_gift(store, clock, chat_id, event_id, callback.from_user.id, attempt_id)
        if start.outcome is not adult.GiftOutcome.START:
            responses = {
                adult.GiftOutcome.DISABLED: texts.GIFT_DISABLED,
                adult.GiftOutcome.NOT_WINNER: texts.GIFT_NOT_WINNER,
                adult.GiftOutcome.IN_PROGRESS: texts.GIFT_IN_PROGRESS,
                adult.GiftOutcome.SENT: texts.GIFT_SENT,
                adult.GiftOutcome.NO_EVENT: texts.HIT_NO_EVENT,
            }
            await callback.answer(responses[start.outcome])
            return
        try:
            # The attempt ends before its lease does, so a retry never overlaps a live attempt.
            async with asyncio.timeout(adult.GIFT_ATTEMPT_DEADLINE.total_seconds()):
                image = await gift_provider.random_image()
                sent = await callback.bot.send_photo(
                    chat_id,
                    photo=BufferedInputFile(image.data, filename=image.filename),
                    caption=texts.GIFT_CAPTION,
                    has_spoiler=True,
                )
        except Exception:
            adult.finish_gift(store, chat_id, event_id, attempt_id, None)
            log.warning("gift for event %s is unavailable", event_id, exc_info=True)
            await _late_answer(callback, texts.GIFT_UNAVAILABLE, show_alert=True)
            return
        # Record the photo before answering: a failed late answer must not leave it "sending".
        finished = adult.finish_gift(store, chat_id, event_id, attempt_id, sent.message_id)
        await _late_answer(callback)
        if not finished:
            log.warning("gift for event %s finished under a superseded attempt", event_id)
            return
        event = store.get_event(event_id)
        if event is not None and event.status == "open":
            await _refresh_arrival(callback, store, event_id, clock, content.quests)
        else:
            await _mark_flown(callback, store, event_id, clock, content.quests)

    @router.callback_query(F.data.startswith("pg:shoo:"))
    async def shoo_callback(callback: CallbackQuery) -> None:
        event_id = _parse_event_id(callback.data, "shoo")
        if callback.message is None or event_id is None:
            await callback.answer(texts.HIT_NO_EVENT)
            return
        chat_id = callback.message.chat.id
        user_id = callback.from_user.id
        outcome = shoo(
            store,
            clock,
            rng,
            content.shoos,
            chat_id,
            event_id,
            user_id,
            callback.from_user.full_name,
            content.quests,
        )

        if outcome is ShooOutcome.STAYED:
            await callback.answer(texts.SHOO_STAYED)
            await _refresh_arrival(callback, store, event_id, clock, content.quests)
        elif outcome is ShooOutcome.FLEW:
            earned = achievements.after_scare(store, clock, chat_id, user_id)
            await callback.answer(texts.with_awards(texts.SHOO_FLEW, award_names(earned)))
            await _mark_flown(callback, store, event_id, clock, content.quests)
        elif outcome is ShooOutcome.ALREADY:
            await callback.answer(texts.SHOO_ALREADY)
        elif outcome is ShooOutcome.CLOSED:
            await callback.answer(texts.HIT_CLOSED)
            await _mark_flown(callback, store, event_id, clock, content.quests)
        else:
            await callback.answer(texts.HIT_NO_EVENT)

    @router.callback_query(F.data.startswith("pg:tz:"))
    async def timezone_callback(callback: CallbackQuery) -> None:
        if callback.message is None or callback.data is None:
            await callback.answer()
            return
        chat_id = callback.message.chat.id
        if not await _is_chat_admin(callback.bot, chat_id, callback.from_user.id):
            await callback.answer(texts.ADMIN_ONLY)
            return
        tz = callback.data.removeprefix("pg:tz:")
        if tz not in content.timezones.values():
            await callback.answer(texts.TZ_UNKNOWN)
            return
        store.enable_chat(chat_id, clock.now(), tz)
        await callback.answer()
        await _edit(callback.message, texts.SETUP_DONE.format(tz=tz), None)

    @router.callback_query(F.data.startswith("pg:"))
    async def retired_button(callback: CallbackQuery) -> None:
        # Buttons of closed mechanics can still sit in old messages.
        await callback.answer(texts.BUTTON_RETIRED)

    @router.message(F.migrate_to_chat_id)
    async def chat_migrated_out(message: Message) -> None:
        _migrate(store, message.chat.id, message.migrate_to_chat_id)

    @router.message(F.migrate_from_chat_id)
    async def chat_migrated_in(message: Message) -> None:
        _migrate(store, message.migrate_from_chat_id, message.chat.id)

    return router


async def _late_answer(callback: CallbackQuery, text: str | None = None, **kwargs) -> None:
    # The only answer to a gift tap comes after the download, when the query may be too old.
    try:
        await callback.answer(text, **kwargs)
    except Exception:
        # Never let the answer stop the redraw: the gift state is already saved.
        log.warning("late answer to a gift tap failed", exc_info=True)


def _event_id(value: str) -> int | None:
    if EVENT_ID.fullmatch(value) is None or int(value) > MAX_EVENT_ID:
        return None
    return int(value)


def _parse_event_id(data: str | None, action: str) -> int | None:
    """Event id of `pg:<action>:<id>`, or None for anything malformed."""
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[:2] != ["pg", action]:
        return None
    return _event_id(parts[2])


async def _is_chat_admin(bot, chat_id: int, user_id: int) -> bool:
    member = await bot.get_chat_member(chat_id, user_id)
    return member.status in ADMIN_STATUSES


def _apply_pause(store: Store, clock: Clock, chat_id: int, args: list[str]) -> str:
    arg = args[0].lower() if args else ""
    if arg == "off":
        store.set_paused_until(chat_id, None)
        return texts.PAUSE_OFF
    if arg not in PAUSE_DURATIONS:
        return texts.PAUSE_BAD
    until = clock.now() + PAUSE_DURATIONS[arg]
    store.set_paused_until(chat_id, until)
    local = limits.chat_local(store.get_chat(chat_id), until)
    return texts.PAUSE_SET.format(until=local.strftime("%d.%m %H:%M"))


def _migrate(store: Store, old_chat_id: int, new_chat_id: int) -> None:
    # Telegram announces a migration in both chats, so the second call finds nothing to move.
    if store.migrate_chat(old_chat_id, new_chat_id):
        log.info("chat %s migrated to %s", old_chat_id, new_chat_id)
    elif store.get_chat(old_chat_id) is not None:
        log.warning("chat %s not migrated: %s is already set up", old_chat_id, new_chat_id)


def _quiet_bounds(args: list[str]) -> tuple[int, int] | None:
    if len(args) != 2 or not all(a.isdecimal() for a in args):
        return None
    start, end = int(args[0]), int(args[1])
    if not (0 <= start <= 23 and 0 <= end <= 23 and end < start):
        return None
    return start, end


async def _refresh_arrival(
    callback: CallbackQuery, store: Store, event_id: int, clock, quests
) -> None:
    message = callback.message
    event = store.get_event(event_id)
    chat = store.get_chat(callback.message.chat.id) if callback.message else None
    if message is None or event is None or chat is None:
        return
    await _edit(
        message,
        game.arrival_text(store, event, clock.now(), quests),
        arrival_keyboard(event, chat.adult_enabled),
    )


async def _mark_flown(callback: CallbackQuery, store: Store, event_id: int, clock, quests) -> None:
    message = callback.message
    event = store.get_event(event_id)
    chat = store.get_chat(callback.message.chat.id) if callback.message else None
    if message is None or event is None or chat is None:
        return
    await _edit(
        message,
        game.flown_text(store, event, clock.now(), quests),
        gift_keyboard(event, chat.adult_enabled),
    )


async def _edit(message, text: str, keyboard) -> None:
    try:
        await message.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise
