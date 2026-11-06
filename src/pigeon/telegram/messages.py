"""Telegram message helpers shared by handlers and the scheduler. The only place that sends
arrival messages, so manual and automatic arrivals look and behave the same."""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from pigeon.render import texts
from pigeon.repo.store import Event
from pigeon.services import adult

TIMEZONES_PER_ROW = 2


def gift_keyboard(event: Event, adult_enabled: bool) -> InlineKeyboardMarkup | None:
    if not adult.gift_visible(event, adult_enabled):
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.GIFT_BUTTON, callback_data=f"pg:gift:{event.id}")]
        ]
    )


def arrival_keyboard(event: Event, adult_enabled: bool = False) -> InlineKeyboardMarkup:
    if event.pigeon_type == "adult":
        rows = [
            [
                InlineKeyboardButton(
                    text=texts.AREA_BUTTONS[area], callback_data=f"pg:aim:{event.id}:{area}"
                )
                for area in adult.AREAS
            ],
            [InlineKeyboardButton(text=texts.SHOO_BUTTON, callback_data=f"pg:shoo:{event.id}")],
        ]
    else:
        rows = [
            [
                InlineKeyboardButton(text=texts.PLUCK_BUTTON, callback_data=f"pg:hit:{event.id}"),
                InlineKeyboardButton(text=texts.SHOO_BUTTON, callback_data=f"pg:shoo:{event.id}"),
            ]
        ]
    gift = gift_keyboard(event, adult_enabled)
    if gift is not None:
        rows.extend(gift.inline_keyboard)
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
            target=adult.target(event) if event.pigeon_type == "adult" else None,
        )
        if self._store is not None and self._clock is not None:
            body = game.arrival_text(self._store, event, self._clock.now(), self._quests)
        adult_enabled = (
            self._store.get_chat(chat_id).adult_enabled if self._store is not None else False
        )
        sent = await self._bot.send_message(
            chat_id,
            body,
            reply_markup=arrival_keyboard(event, adult_enabled),
        )
        return sent.message_id

    async def send_text(self, chat_id: int, text: str) -> None:
        await self._bot.send_message(chat_id, text)

    async def mark_flown(self, chat_id: int, message_id: int, text: str) -> None:
        keyboard = None
        if self._store is not None:
            event = self._store.event_by_message(chat_id, message_id)
            chat = self._store.get_chat(chat_id)
            if event is not None and chat is not None:
                keyboard = gift_keyboard(event, chat.adult_enabled)
        try:
            await self._bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, reply_markup=keyboard
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise
