"""Telegram handlers: /pigeon commands and the hit button. Thin: logic lives in services."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.exceptions import AiogramError, TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message, User

from pigeon.budget import limits
from pigeon.clock import Clock
from pigeon.content import Content
from pigeon.render import texts
from pigeon.repo.store import Store, minute_key
from pigeon.services import achievements, bets, darts, duels, fortune, game, name_vote, photo
from pigeon.telegram.duels import Sleep, run_bout
from pigeon.telegram.messages import TelegramArrivalSender, arrival_keyboard, duel_keyboard

log = logging.getLogger("pigeon.handlers")

GROUP_TYPES = {"group", "supergroup"}
ADMIN_STATUSES = {"creator", "administrator"}
DEFAULT_TIMEZONE = "UTC"
PAUSE_DURATIONS = {"24h": timedelta(hours=24), "7d": timedelta(days=7)}


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
    sleep: Sleep = asyncio.sleep,
) -> Router:
    router = Router(name="pigeon")

    def award_names(codes: list[str]) -> list[str]:
        return [content.achievements[code].name for code in codes]

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
            current = store.get_chat(chat_id)
            tz = parts[1] if len(parts) > 1 else (current.timezone if current else DEFAULT_TIMEZONE)
            if not _valid_timezone(tz):
                await message.answer(texts.TZ_BAD)
                return
            store.enable_chat(chat_id, clock.now(), tz)
            await message.answer(texts.SETUP_DONE.format(tz=tz))
            return

        if not store.is_enabled(chat_id):
            await message.answer(texts.NOT_ENABLED)
            return

        _remember(store, chat_id, message.from_user)

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
                )
            )
            return

        if sub == "fortune":
            line = fortune.draw(
                store, clock, rng, content.fortunes, store.get_chat(chat_id), message.from_user.id
            )
            await message.reply(texts.FORTUNE_ALREADY if line is None else f"🔮 {line}")
            return

        if sub == "stats":
            rows = store.leaderboard(chat_id)
            await message.answer(texts.stats(rows) if rows else texts.NO_STATS)
            return

        if sub == "duel":
            await _challenge(store, clock, message, parts[1:])
            return

        if sub == "photo":
            reply = message.reply_to_message
            if reply is None or not reply.photo:
                await message.answer(texts.PHOTO_USAGE)
            elif not photo.claim(store, clock, chat_id):
                await message.answer(texts.PHOTO_COOLDOWN)
            else:
                await _send_photo_poll(message, reply.message_id)
            return

        if sub == "name":
            if not await is_admin(message):
                await message.answer(texts.ADMIN_ONLY)
                return
            outcome = await name_vote.start(
                store, clock, rng, TelegramArrivalSender(message.bot), content.names, chat_id
            )
            if outcome is name_vote.Start.ALREADY:
                await message.answer(texts.NAME_ALREADY)
            elif outcome is name_vote.Start.NO_OPTIONS:
                await message.answer(texts.NAME_NO_OPTIONS)
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
        _remember(store, chat_id, callback.from_user)
        event_id = int(callback.data.rsplit(":", 1)[1])
        name = callback.from_user.full_name
        outcome = game.hit(store, clock, chat_id, event_id, callback.from_user.id, name)

        if outcome is game.HitOutcome.HIT:
            earned = achievements.after_hit(store, clock, chat_id, callback.from_user.id)
            await callback.answer(texts.with_awards(texts.HIT_OK, award_names(earned)))
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
        _remember(store, chat_id, callback.from_user)
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
        earned = achievements.after_dart(store, clock, chat_id, user_id, value)
        result = texts.DART_RESULT.format(value=value, points=points)
        await throw.reply(texts.with_awards(result, award_names(earned)))

    @router.callback_query(F.data.startswith("pg:bet:"))
    async def bet_callback(callback: CallbackQuery) -> None:
        if callback.message is None or callback.data is None:
            await callback.answer()
            return
        chat_id = callback.message.chat.id
        _remember(store, chat_id, callback.from_user)
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

    @router.callback_query(F.data.startswith("pg:duel_ok:"))
    async def duel_accept_callback(callback: CallbackQuery) -> None:
        if callback.message is None or callback.data is None:
            await callback.answer()
            return
        chat_id = callback.message.chat.id
        duel_id = int(callback.data.rsplit(":", 1)[1])
        answer = duels.accept(store, clock, chat_id, duel_id, callback.from_user.id)
        if answer is not duels.Answer.OK:
            await callback.answer(_DUEL_REFUSALS[answer])
            return
        duel = store.get_duel(chat_id, duel_id)
        await _settle_duel_message(
            callback,
            texts.DUEL_FIGHT.format(
                a=duels.member_name(store, chat_id, duel.challenger_id),
                b=duels.member_name(store, chat_id, duel.opponent_id),
            ),
        )
        await run_bout(callback.bot, store, clock, duel, sleep, award_names)

    @router.callback_query(F.data.startswith("pg:duel_no:"))
    async def duel_decline_callback(callback: CallbackQuery) -> None:
        if callback.message is None or callback.data is None:
            await callback.answer()
            return
        chat_id = callback.message.chat.id
        duel_id = int(callback.data.rsplit(":", 1)[1])
        answer = duels.decline(store, clock, chat_id, duel_id, callback.from_user.id)
        if answer is not duels.Answer.OK:
            await callback.answer(_DUEL_REFUSALS[answer])
            return
        await _settle_duel_message(
            callback,
            texts.DUEL_DECLINED.format(b=duels.member_name(store, chat_id, callback.from_user.id)),
        )

    @router.message(F.migrate_to_chat_id)
    async def chat_migrated_out(message: Message) -> None:
        _migrate(store, message.chat.id, message.migrate_to_chat_id)

    @router.message(F.migrate_from_chat_id)
    async def chat_migrated_in(message: Message) -> None:
        _migrate(store, message.migrate_from_chat_id, message.chat.id)

    @router.message(F.chat.type.in_(GROUP_TYPES))
    async def on_group_message(message: Message) -> None:
        # Live-chat check: only a per-minute count is kept, never the message text.
        # Runs after the /pigeon command handler, so commands are not counted.
        chat_id = message.chat.id
        if not store.is_enabled(chat_id):
            return
        store.record_message(chat_id, minute_key(clock.now()))
        _remember(store, chat_id, message.from_user)
        if message.photo and photo.claim_for_caption(
            store, clock, store.get_chat(chat_id), message.caption
        ):
            await _send_photo_poll(message, message.message_id)

    return router


_DUEL_REFUSALS = {
    duels.Answer.NOT_OPPONENT: texts.DUEL_NOT_YOURS,
    duels.Answer.GONE: texts.DUEL_GONE,
}


def _remember(store: Store, chat_id: int, user: User | None) -> None:
    # Keeps @username lookups current. Bots are not members; a chat without setup stores nothing.
    if user is not None and not user.is_bot and store.is_enabled(chat_id):
        store.remember_member(chat_id, user.id, user.full_name, user.username)


def _duel_target(store: Store, message: Message, args: list[str]) -> tuple[int | None, str | None]:
    """The opponent named by a command: (user_id, None), or (None, a text explaining why not)."""
    chat_id = message.chat.id
    target = next((e.user for e in message.entities or [] if e.type == "text_mention"), None)
    reply = message.reply_to_message
    # In a forum, a plain message counts as a reply to the topic's opening service message.
    if target is None and not args and reply is not None and not reply.forum_topic_created:
        target = reply.from_user
    if target is not None:
        if target.is_bot:
            return None, texts.DUEL_BOT
        _remember(store, chat_id, target)
        return target.id, None
    if not args:
        return None, texts.DUEL_USAGE
    user_id = store.member_by_username(chat_id, args[0].removeprefix("@"))
    return (user_id, None) if user_id is not None else (None, texts.DUEL_UNKNOWN)


async def _challenge(store: Store, clock: Clock, message: Message, args: list[str]) -> None:
    chat_id = message.chat.id
    challenger = message.from_user
    if challenger.is_bot:  # an anonymous admin posts as a shared bot account
        await message.answer(texts.DUEL_BOT)
        return
    opponent_id, refusal = _duel_target(store, message, args)
    if refusal is None and opponent_id == challenger.id:
        refusal = texts.DUEL_SELF
    if refusal is not None:
        await message.answer(refusal)
        return
    duel_id = duels.challenge(store, clock, chat_id, challenger.id, opponent_id)
    if duel_id is None:
        await message.answer(texts.DUEL_BUSY)
        return
    text = texts.DUEL_CHALLENGE.format(
        a=duels.member_name(store, chat_id, challenger.id),
        b=duels.member_name(store, chat_id, opponent_id),
    )
    try:
        sent = await message.answer(text, reply_markup=duel_keyboard(duel_id))
    except Exception:
        store.delete_duel(chat_id, duel_id)  # nobody can answer it, so it must not hold the chat
        raise
    store.set_duel_message(chat_id, duel_id, sent.message_id)


async def _settle_duel_message(callback: CallbackQuery, text: str) -> None:
    """Confirm the tap and update the challenge. The answer is already stored, so neither call
    may stop what follows: a tap read late from a restart backlog cannot be confirmed at all."""
    try:
        await callback.answer(texts.DUEL_ANSWERED)
    except AiogramError as exc:
        log.warning("could not confirm a duel answer: %s", exc)
    try:
        await callback.message.edit_text(text, reply_markup=None)
    except AiogramError as exc:
        log.warning("could not update a duel message: %s", exc)


async def _send_photo_poll(message: Message, photo_message_id: int) -> None:
    await TelegramArrivalSender(message.bot).send_poll(
        message.chat.id, texts.PHOTO_QUESTION, texts.PHOTO_OPTIONS, reply_to=photo_message_id
    )


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
