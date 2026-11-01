"""Telegram message helpers shared by handlers and the scheduler. The only place that sends
arrival messages, so manual and automatic arrivals look and behave the same."""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from pigeon.render import texts
from pigeon.repo.store import Event


def pluck_keyboard(event_id: int) -> InlineKeyboardMarkup:
    button = InlineKeyboardButton(text=texts.PLUCK_BUTTON, callback_data=f"pg:hit:{event_id}")
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


class TelegramArrivalSender:
    """Implements scheduler.arrivals.ArrivalSender on top of aiogram."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_arrival(self, chat_id: int, event: Event) -> int:
        sent = await self._bot.send_message(
            chat_id,
            texts.ARRIVAL.format(count=0),
            reply_markup=pluck_keyboard(event.id),
        )
        return sent.message_id

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
