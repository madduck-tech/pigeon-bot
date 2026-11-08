"""The command menu is published at startup for two scopes, and its failure never stops polling."""

import logging
import re
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SetMyCommands

from pigeon import app
from pigeon.config import Settings
from pigeon.render import texts
from pigeon.telegram.commands import publish_menus

COMMON = [name for name, _ in texts.COMMON_COMMANDS]
ADMIN = [name for name, _ in texts.ADMIN_COMMANDS]


class MenuSession(BaseSession):
    def __init__(self, fail: set[str] | None = None, error=TelegramBadRequest) -> None:
        super().__init__()
        self.calls = []
        self.fail = fail or set()
        self.error = error

    async def close(self) -> None:
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SetMyCommands) and method.scope.type in self.fail:
            if self.error is TelegramNetworkError:
                raise TelegramNetworkError(method, "timeout")
            raise TelegramBadRequest(method, "Bad Request: BOT_COMMAND_INVALID")
        return True

    async def stream_content(self, *args, **kwargs) -> AsyncGenerator[bytes, None]:
        yield b""

    def menus(self) -> dict[str, list[str]]:
        return {
            c.scope.type: [command.command for command in c.commands]
            for c in self.calls
            if isinstance(c, SetMyCommands)
        }


_build_bot = app.build_bot


def _bot(session):
    bot = _build_bot("42:TEST")
    bot.session = session
    return bot


async def test_members_see_common_commands_and_admins_see_all() -> None:
    session = MenuSession()
    await publish_menus(_bot(session))
    assert session.menus() == {
        "all_group_chats": COMMON,
        "all_chat_administrators": COMMON + ADMIN,
    }
    for call in session.calls:
        assert call.language_code is None  # one Russian menu, no localisation
        for command in call.commands:
            assert re.fullmatch(r"[a-z0-9_]{1,32}", command.command)
            assert 1 <= len(command.description) <= 256


@pytest.mark.parametrize(
    "fail",
    [
        {"all_group_chats"},
        {"all_chat_administrators"},
        {"all_group_chats", "all_chat_administrators"},
    ],
)
@pytest.mark.parametrize("error", [TelegramBadRequest, TelegramNetworkError])
async def test_a_failed_menu_is_logged_and_the_other_is_still_set(fail, error, caplog) -> None:
    session = MenuSession(fail, error)
    with caplog.at_level(logging.ERROR, logger="pigeon.telegram"):
        await publish_menus(_bot(session))
    assert set(session.menus()) == {"all_group_chats", "all_chat_administrators"}
    failed = [r for r in caplog.records if "command menu" in r.getMessage()]
    assert sorted(r.args[0] for r in failed) == sorted(fail)
    assert all(r.exc_info for r in failed)


@pytest.mark.parametrize("fail", [set(), {"all_group_chats", "all_chat_administrators"}])
async def test_polling_starts_after_the_menu_whatever_it_returns(monkeypatch, tmp_path, fail):
    session = MenuSession(fail)
    order = []

    async def fake_polling(self, bot, **kwargs) -> None:
        order.append(("polling", len(session.menus())))

    monkeypatch.setattr(app, "build_bot", lambda token: _bot(session))
    monkeypatch.setattr(app.Dispatcher, "start_polling", fake_polling)
    settings = Settings(
        bot_token="42:TEST",
        db_path=tmp_path / "pigeon.db",
        content_dir=Path(__file__).resolve().parents[1] / "content",
    )
    await app.run_bot(settings)
    assert order == [("polling", 2)]  # both menus were attempted before polling began
