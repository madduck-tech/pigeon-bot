"""Recognize the specific Telegram permission failure at scheduler API calls."""

from collections.abc import Awaitable

from aiogram.exceptions import TelegramBadRequest


class InsufficientRights(Exception):
    """Telegram refused a scheduler operation for insufficient rights."""


async def check_rights[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except TelegramBadRequest as exc:
        if "not enough rights" in exc.message.casefold():
            raise InsufficientRights from exc
        raise
