"""Usage hooks for inbound buttons and every outgoing Telegram delivery attempt."""

import logging
from contextvars import ContextVar

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from pigeon.services.usage import Usage

callback_chat: ContextVar[int | None] = ContextVar("usage_callback_chat", default=None)
GROUP_TYPES = {"group", "supergroup"}
BUTTONS = {"hit", "aim", "shoo", "gift", "tz"}
log = logging.getLogger("pigeon.usage")


class ButtonUsage(BaseMiddleware):
    def __init__(self, usage: Usage, store) -> None:
        self.usage = usage
        self.store = store

    def recognized(self, event: CallbackQuery, chat_id: int) -> bool:
        parts = (event.data or "").split(":")
        if len(parts) < 3 or parts[0] != "pg" or parts[1] not in BUTTONS:
            return False
        if parts[1] == "tz":
            return len(parts) == 3
        if len(parts) != (4 if parts[1] == "aim" else 3):
            return False
        value = parts[2]
        if not value.isascii() or not value.isdecimal() or len(value) > 19:
            return False
        event_id = int(value)
        if event_id > 2**63 - 1:
            return False
        arrival = self.store.get_event(event_id)
        return arrival is not None and arrival.chat_id == chat_id

    async def __call__(self, handler, event: CallbackQuery, data):
        message = event.message
        chat_id = message.chat.id if message and message.chat.type in GROUP_TYPES else None
        token = callback_chat.set(chat_id)
        try:
            if chat_id is not None and not event.from_user.is_bot:
                try:
                    recognized = self.recognized(event, chat_id)
                except Exception:
                    log.warning("usage button lookup failed", exc_info=True)
                    recognized = False
                if recognized:
                    self.usage.record(chat_id, user_id=event.from_user.id)
            return await handler(event, data)
        finally:
            callback_chat.reset(token)


class DeliveryUsage:
    def __init__(self, usage: Usage) -> None:
        self.usage = usage

    async def __call__(self, make_request, bot, method):
        try:
            return await make_request(bot, method)
        except Exception as exc:
            name = method.__api_method__
            is_callback = name == "answerCallbackQuery"
            is_message = name.startswith(("send", "editMessage"))
            chat_id = callback_chat.get() if is_callback else getattr(method, "chat_id", None)
            unchanged = isinstance(exc, TelegramBadRequest) and "message is not modified" in str(
                exc
            )
            if (
                (is_callback or is_message)
                and isinstance(chat_id, int)
                and chat_id < 0
                and not unchanged
            ):
                self.usage.record(
                    chat_id, "delivery.callback_error" if is_callback else "delivery.message_error"
                )
            raise


def install_delivery_usage(bot: Bot, usage: Usage) -> None:
    bot.session.middleware(DeliveryUsage(usage))
