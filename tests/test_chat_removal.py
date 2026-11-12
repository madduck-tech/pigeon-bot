"""Removal updates and Forbidden replies stop only the affected chat."""

import logging
import random
from datetime import UTC, datetime, timedelta

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from aiogram.types import Update

from pigeon.clock import FixedClock
from pigeon.handlers.pigeon import build_router
from pigeon.repo.store import Store
from pigeon.scheduler import arrivals, fortunes
from pigeon.services import game
from rigged import CONTENT, EveryDay

CHAT = -2002  # sorted first, to prove the next chat is still processed
OTHER = -2001
NOW = datetime(2026, 11, 3, 10, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "removal.db")
    for chat_id in (CHAT, OTHER):
        result.enable_chat(chat_id, NOW, "Europe/Moscow")
    yield result
    result.close()


class NoRequests(BaseSession):
    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        raise AssertionError(f"Unexpected API request: {type(method).__name__}")

    async def stream_content(self, *args, **kwargs):
        yield b""


async def membership(dp, bot, status, chat_type="supergroup", update_id=1, chat_id=CHAT):
    user = {"id": 42, "is_bot": True, "first_name": "Pigeon"}
    member = {"status": status, "user": user}
    if status == "administrator":
        member.update(
            can_be_edited=False,
            is_anonymous=False,
            can_manage_chat=True,
            can_delete_messages=True,
            can_manage_video_chats=True,
            can_restrict_members=True,
            can_promote_members=False,
            can_change_info=True,
            can_invite_users=True,
            can_post_stories=False,
            can_edit_stories=False,
            can_delete_stories=False,
            can_send_welcome_messages=False,
        )
    if status == "kicked":
        member["until_date"] = 0
    await dp.feed_update(
        bot,
        Update.model_validate(
            {
                "update_id": update_id,
                "my_chat_member": {
                    "chat": {"id": chat_id, "type": chat_type},
                    "from": {"id": 1, "is_bot": False, "first_name": "Admin"},
                    "date": int(NOW.timestamp()),
                    "old_chat_member": {"status": "member", "user": user},
                    "new_chat_member": member,
                },
            },
            context={"bot": bot},
        ),
    )


@pytest.mark.parametrize("status", ["left", "kicked"])
@pytest.mark.parametrize("chat_type", ["group", "supergroup"])
async def test_removal_update_preserves_history_and_requires_setup(store, status, chat_type):
    event = game.arrive(store, FixedClock(NOW), CHAT)
    store.upsert_member(CHAT, 7, "Маша")
    store.add_hit(CHAT, event.id, 7, NOW, 3)
    store.award(CHAT, 7, "test-achievement", NOW)
    bot = Bot("42:TEST", session=NoRequests())
    dp = Dispatcher()
    dp.include_router(build_router(store, FixedClock(NOW), CONTENT, random.Random(1)))
    try:
        await membership(dp, bot, status, chat_type)
        await membership(dp, bot, status, chat_type, 2)  # duplicate is harmless
        assert [chat.chat_id for chat in store.enabled_chats()] == [OTHER]
        assert store.get_event(event.id) == event
        assert store.member_name(CHAT, 7) == "Маша"
        assert store.balance(CHAT, 7) == 3
        assert "test-achievement" in store.achievements_of(CHAT, 7)
        await membership(dp, bot, "member", chat_type, 3)
        assert not store.is_enabled(CHAT)
        store.enable_chat(CHAT, NOW, "Europe/Moscow")  # the existing /setup transition
        assert store.is_enabled(CHAT)
        assert store.get_event(event.id) == event
    finally:
        await bot.session.close()


@pytest.mark.parametrize("status", ["member", "administrator"])
async def test_active_membership_update_does_not_disable(store, status):
    bot = Bot("42:TEST", session=NoRequests())
    dp = Dispatcher()
    dp.include_router(build_router(store, FixedClock(NOW), CONTENT, random.Random(1)))
    try:
        await membership(dp, bot, status)
        assert store.is_enabled(CHAT)
    finally:
        await bot.session.close()


class Sender:
    def __init__(self, forbidden=True):
        self.calls = []
        self.forbidden = forbidden

    async def attempt(self, chat_id):
        self.calls.append(chat_id)
        if chat_id == CHAT:
            if self.forbidden:
                raise TelegramForbiddenError(
                    method=SendMessage(chat_id=chat_id, text="test"),
                    message="Forbidden: bot was kicked from the group chat",
                )
            raise RuntimeError("network unavailable")
        return 123

    async def send_arrival(self, chat_id, event):
        return await self.attempt(chat_id)

    async def mark_flown(self, chat_id, message_id, text):
        await self.attempt(chat_id)

    async def send_text(self, chat_id, text):
        await self.attempt(chat_id)


@pytest.mark.parametrize("path", ["arrival", "flown", "fortune"])
@pytest.mark.parametrize("forbidden", [True, False])
async def test_scheduler_failure_isolated_and_forbidden_not_retried(store, caplog, path, forbidden):
    clock = FixedClock(NOW)
    sender = Sender(forbidden)
    if path == "flown":
        for chat_id in (CHAT, OTHER):
            event = game.arrive(store, FixedClock(NOW - timedelta(hours=1)), chat_id)
            store.set_event_message(event.id, 10)
    if path != "fortune":
        for chat_id in (CHAT, OTHER):
            store.save_plan(
                chat_id, "2026-11-03", NOW, "pending" if path == "arrival" else "skipped"
            )

    async def tick():
        if path == "fortune":
            await fortunes.tick(store, clock, random.Random(1), sender, CONTENT.fortunes)
        else:
            await arrivals.tick(store, clock, EveryDay(1), sender, CONTENT)

    with caplog.at_level(logging.WARNING):
        await tick()
    assert sender.calls == [CHAT, OTHER]
    assert store.is_enabled(CHAT) is (not forbidden)
    assert store.is_enabled(OTHER)
    if forbidden:
        records = [record for record in caplog.records if str(CHAT) in record.getMessage()]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert records[0].exc_info is None
        sender.calls.clear()
        await tick()
        assert CHAT not in sender.calls
        assert [chat.chat_id for chat in store.enabled_chats()] == [OTHER]


async def test_forbidden_disables_even_if_arrival_was_already_delivered(store, monkeypatch):
    # A racing click may establish delivery before send_arrival loses its response.
    monkeypatch.setattr(game, "delete_unsent_event", lambda *args: False)
    for chat_id in (CHAT, OTHER):
        store.save_plan(chat_id, "2026-11-03", NOW, "pending")
    sender = Sender()
    await arrivals.tick(store, FixedClock(NOW), EveryDay(1), sender, CONTENT)
    assert not store.is_enabled(CHAT)
    assert store.is_enabled(OTHER)
    assert store.current_open_event(CHAT) is not None
    assert sender.calls == [CHAT, OTHER]


async def test_chronicle_skips_disabled_chat(store, monkeypatch):
    from pigeon.scheduler import chronicle

    store.disable_chat(CHAT)
    calls = []

    async def prepare(store, clock, model, prompt, chat, quests):
        calls.append(chat.chat_id)

    monkeypatch.setattr(chronicle.chronicle, "prepare", prepare)
    await chronicle.tick(store, FixedClock(NOW), None, None, CONTENT.quests)
    assert calls == [OTHER]


@pytest.mark.parametrize("chat_id,chat_type", [(CHAT, "private"), (-9999, "supergroup")])
async def test_unrelated_membership_is_harmless(store, chat_id, chat_type):
    bot = Bot("42:TEST", session=NoRequests())
    dp = Dispatcher()
    dp.include_router(build_router(store, FixedClock(NOW), CONTENT, random.Random(1)))
    try:
        await membership(dp, bot, "left", chat_type, chat_id=chat_id)
        assert [chat.chat_id for chat in store.enabled_chats()] == [CHAT, OTHER]
        if chat_id != CHAT:
            assert store.get_chat(chat_id) is None
    finally:
        await bot.session.close()


async def test_monday_post_with_stored_chronicle_disables_forbidden_chat(store):
    monday = datetime(2026, 11, 9, 7, tzinfo=UTC)
    for chat_id in (CHAT, OTHER):
        assert store.claim_chronicle(chat_id, "2026-11-09", "pending", monday)
        store.finish_chronicle(chat_id, "2026-11-09", "Голуби держались бодро.")
    sender = Sender()
    await fortunes.tick(
        store,
        FixedClock(monday),
        random.Random(1),
        sender,
        CONTENT.fortunes,
        CONTENT.names,
        CONTENT.quests,
    )
    assert sender.calls == [CHAT, OTHER]
    assert not store.is_enabled(CHAT)
    assert store.is_enabled(OTHER)
    issue = store.get_fortune_issue(CHAT, 0, "2026-11-09")
    assert "Голуби держались бодро." in issue.rendered_text
    assert issue.sent_at is None
    assert store.get_fortune_issue(OTHER, 0, "2026-11-09").sent_at is not None
