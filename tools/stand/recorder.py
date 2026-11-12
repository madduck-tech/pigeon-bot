"""Request middleware that records every Bot API call and guards the stand.

It sits on the bot's session, so it sees every call the bot makes: from handlers and from the
harness alike. Nothing in pigeon/ knows about it.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import enum
from typing import Any

from aiogram.client.default import Default
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramRetryAfter
from pydantic import BaseModel

# Telegram's answer to an answerCallbackQuery for a query it never sent.
UNKNOWN_QUERY_MARKERS = ("query is too old", "query id is invalid")
EXPECTED_ANSWER_ERROR = "synthetic callback_query: Telegram does not know this query id"
CHAT_FIELDS = ("chat_id", "from_chat_id")
# Flood control on a busy stand chat: wait as Telegram asks, a bounded number of times.
MAX_FLOOD_WAITS = 3
MAX_FLOOD_WAIT_SECONDS = 60


class StandGuardError(Exception):
    """A call the stand refuses to send to Telegram."""


def plain(value: Any, bot=None) -> Any:
    """JSON-ready copy of an aiogram object, with Bot API field names and defaults resolved."""
    if isinstance(value, Default):
        return plain(bot.default[value.name], bot) if bot is not None else None
    if isinstance(value, BaseModel):
        result = {}
        for name, info in type(value).model_fields.items():
            item = plain(getattr(value, name), bot)
            if item is not None:
                result[info.alias or name] = item
        return result
    if isinstance(value, dict):
        return {k: plain(v, bot) for k, v in value.items() if v is not None}
    if isinstance(value, list | tuple):
        return [plain(v, bot) for v in value]
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dt.datetime):
        return int(value.timestamp())
    if isinstance(value, dt.timedelta):
        return int(value.total_seconds())
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return repr(value)


def is_unknown_query(message: str) -> bool:
    text = message.lower()
    return any(marker in text for marker in UNKNOWN_QUERY_MARKERS)


class Recorder(BaseRequestMiddleware):
    """Records calls into the current step and blocks what the stand must never send."""

    def __init__(self, chat_id: int) -> None:
        self.chat_id = chat_id
        self.verified = False  # set once getMe proved this is not the production bot
        self.synthetic_queries: set[str] = set()
        self.calls: list[dict[str, Any]] = []  # the current step's calls
        self.sleep = asyncio.sleep

    async def __call__(self, make_request, bot, method):
        name = method.__api_method__
        record: dict[str, Any] = {"method": name, "params": plain(method, bot)}
        self.calls.append(record)

        reason = self._refusal(name, record["params"])
        if reason is not None:
            record["ok"] = False
            record["blocked"] = reason
            raise StandGuardError(f"{name}: {reason}")

        try:
            result = await self._with_flood_waits(make_request, bot, method, record)
        except TelegramAPIError as exc:
            record["ok"] = False
            record["error"] = exc.message
            record["error_type"] = type(exc).__name__
            if isinstance(exc, TelegramNetworkError):
                record["network"] = True
            if (
                name == "answerCallbackQuery"
                and record["params"].get("callback_query_id") in self.synthetic_queries
                and is_unknown_query(exc.message)
            ):
                # The handler must go on as after a real answer, or it never edits the message.
                record["expected_error"] = EXPECTED_ANSWER_ERROR
                return True
            raise
        record["ok"] = True
        record["result"] = plain(result, bot)
        return result

    async def _with_flood_waits(self, make_request, bot, method, record):
        """Flood control says nothing about the change under test, so wait and resend.

        Every wait is in the report; a wait longer than the bound fails the call.
        """
        for _ in range(MAX_FLOOD_WAITS):
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as exc:
                if exc.retry_after > MAX_FLOOD_WAIT_SECONDS:
                    raise
                record.setdefault("flood_waits", []).append(exc.retry_after)
                await self.sleep(exc.retry_after)
        return await make_request(bot, method)

    def _refusal(self, name: str, params: dict[str, Any]) -> str | None:
        if not self.verified and name != "getMe":
            return "blocked before getMe proved this is the test bot"
        for key in CHAT_FIELDS:
            if key in params and not _same_chat(params[key], self.chat_id):
                return f"{key}={params[key]!r} is not the stand chat"
        return None


def _same_chat(value: Any, chat_id: int) -> bool:
    try:
        return int(value) == chat_id
    except (TypeError, ValueError):
        return False
