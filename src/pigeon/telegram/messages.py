"""Telegram message helpers shared by handlers and the scheduler. The only place that sends
arrival messages, so manual and automatic arrivals look and behave the same."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputPollOption,
    ReplyParameters,
)

from pigeon.render import texts
from pigeon.repo.store import Event

log = logging.getLogger("pigeon.messages")


def arrival_keyboard(event_id: int) -> InlineKeyboardMarkup:
    def button(text: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=f"pg:{action}:{event_id}")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button(texts.PLUCK_BUTTON, "hit"), button(texts.DART_BUTTON, "dart")],
            [
                InlineKeyboardButton(
                    text=texts.BET_GE3_BUTTON, callback_data=f"pg:bet:{event_id}:ge3"
                ),
                InlineKeyboardButton(
                    text=texts.BET_LT3_BUTTON, callback_data=f"pg:bet:{event_id}:lt3"
                ),
            ],
        ]
    )


def duel_keyboard(duel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.DUEL_ACCEPT_BUTTON, callback_data=f"pg:duel_ok:{duel_id}"
                ),
                InlineKeyboardButton(
                    text=texts.DUEL_DECLINE_BUTTON, callback_data=f"pg:duel_no:{duel_id}"
                ),
            ]
        ]
    )


class TelegramArrivalSender:
    """Implements scheduler.arrivals.ArrivalSender on top of aiogram."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_arrival(self, chat_id: int, event: Event) -> int:
        sent = await self._bot.send_message(
            chat_id,
            texts.ARRIVAL.format(count=0),
            reply_markup=arrival_keyboard(event.id),
        )
        return sent.message_id

    async def send_text(self, chat_id: int, text: str) -> None:
        await self._bot.send_message(chat_id, text)

    async def edit_text(self, chat_id: int, message_id: int, text: str) -> None:
        """Replace a message's text and remove its buttons."""
        try:
            await self._bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, reply_markup=None
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise

    async def send_poll(
        self,
        chat_id: int,
        question: str,
        options: Sequence[str],
        reply_to: int | None = None,
    ) -> int:
        """Post an anonymous poll. A reply is silent, so it does not ping the photo's author."""
        reply = None
        if reply_to is not None:
            reply = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True)
        sent = await self._bot.send_poll(
            chat_id,
            question,
            [InputPollOption(text=option) for option in options],
            reply_parameters=reply,
            disable_notification=reply_to is not None,
        )
        return sent.message_id

    async def stop_poll(self, chat_id: int, message_id: int) -> list[int] | None:
        """Close a poll and return its vote counts, or None if the poll is gone."""
        try:
            poll = await self._bot.stop_poll(chat_id, message_id)
        except TelegramBadRequest as exc:
            log.warning("chat %s: could not stop poll %s: %s", chat_id, message_id, exc)
            return None
        return [option.voter_count for option in poll.options]

    async def mark_flown(self, chat_id: int, message_id: int, count: int) -> None:
        try:
            await self._bot.edit_message_text(
                texts.ARRIVAL_FLEW.format(count=count),
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=None,
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise
