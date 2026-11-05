"""Telegram message helpers shared by handlers and the scheduler. The only place that sends
arrival messages, so manual and automatic arrivals look and behave the same."""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from pigeon.render import texts
from pigeon.repo.store import Event

TIMEZONES_PER_ROW = 2


def arrival_keyboard(event_id: int) -> InlineKeyboardMarkup:
    def button(text: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=f"pg:{action}:{event_id}")

    return InlineKeyboardMarkup(
        inline_keyboard=[[button(texts.PLUCK_BUTTON, "hit"), button(texts.SHOO_BUTTON, "shoo")]]
    )


def timezone_keyboard(timezones: dict[str, str]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=label, callback_data=f"pg:tz:{name}")
        for label, name in timezones.items()
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            buttons[i : i + TIMEZONES_PER_ROW] for i in range(0, len(buttons), TIMEZONES_PER_ROW)
        ]
    )


class TelegramArrivalSender:
    """Implements scheduler.arrivals.ArrivalSender on top of aiogram."""

    def __init__(self, bot: Bot, store=None, clock=None, quests=()) -> None:
        self._bot = bot
        self._store = store
        self._clock = clock
        self._quests = quests

    async def send_arrival(self, chat_id: int, event: Event) -> int:
        from pigeon.services import game

        body = texts.arrival(
            event.pigeon_name or texts.PIGEON_NAME,
            event.intro or texts.ARRIVAL_INTRO,
            [],
            0,
            event.pigeon_type,
        )
        if self._store is not None and self._clock is not None:
            body = game.arrival_text(self._store, event, self._clock.now(), self._quests)
        sent = await self._bot.send_message(
            chat_id,
            body,
            reply_markup=arrival_keyboard(event.id),
        )
        return sent.message_id

    async def send_text(self, chat_id: int, text: str) -> None:
        await self._bot.send_message(chat_id, text)

    async def mark_flown(self, chat_id: int, message_id: int, text: str) -> None:
        try:
            await self._bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, reply_markup=None
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise
