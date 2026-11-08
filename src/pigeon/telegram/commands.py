"""The Telegram command menu, published by the bot itself so the list lives in the repository."""

from __future__ import annotations

import logging

from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
)

from pigeon.render import texts

log = logging.getLogger("pigeon.telegram")


def menus() -> list[tuple[object, list[BotCommand]]]:
    """Members of groups see the common commands; chat admins see the admin ones as well."""
    common = [BotCommand(command=name, description=text) for name, text in texts.COMMON_COMMANDS]
    admin = [BotCommand(command=name, description=text) for name, text in texts.ADMIN_COMMANDS]
    return [
        (BotCommandScopeAllGroupChats(), common),
        (BotCommandScopeAllChatAdministrators(), common + admin),
    ]


async def publish_menus(bot) -> None:
    """Set both menus. A failure is logged and never stops the bot: commands work without a menu."""
    for scope, commands in menus():
        try:
            await bot.set_my_commands(commands, scope=scope)
        except Exception:
            log.exception("could not set the command menu for %s", scope.type)
