"""Addition updates reach the real router; migration and shutdown cancel delivery."""

import asyncio

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

from handlers.test_commands import ADMIN, CHAT, CONTENT, NEW_CHAT, NOON, USER, Harness
from pigeon.clock import FixedClock
from pigeon.render import texts
from pigeon.repo.store import Store
from pigeon.services.setup_invitation import SetupInvitations
from services.test_setup_invitation import Sleep, release


@pytest.fixture
async def env(monkeypatch):
    store = Store(":memory:")
    sleep = Sleep()
    inv = SetupInvitations(store, sleep)
    monkeypatch.setattr("pigeon.handlers.pigeon.SetupInvitations", lambda store: inv)
    h = Harness(store, FixedClock(NOON))
    yield h, store, sleep, inv
    await h.dp.emit_shutdown()
    store.close()
    await h.bot.session.close()


def payload(h, chat=CHAT, old="left", new="member", kind="supergroup"):
    def member(status):
        value = {"status": status, "user": {"id": h.bot.id, "is_bot": True, "first_name": "bot"}}
        if status == "kicked":
            value["until_date"] = 0
        return value

    return dict(
        chat={"id": chat, "type": kind},
        date=NOON,
        from_user={"id": ADMIN, "is_bot": False, "first_name": "admin"},
        old_chat_member=member(old),
        new_chat_member=member(new),
    )


@pytest.mark.parametrize("kind", ["group", "supergroup"])
async def test_addition_and_existing_timezone_permissions(env, kind):
    h, store, sleep, inv = env
    assert "my_chat_member" in h.dp.resolve_used_update_types()
    await h._feed(my_chat_member=payload(h, kind=kind))
    await asyncio.sleep(0)
    assert h.session.said() == []
    # Other updates do not wait on the invitation's five-second delay.
    await h.command("/help")
    assert h.session.said() == [texts.help_text()]
    await release(sleep, inv)
    assert h.session.said()[-1] == texts.SETUP_CHOOSE
    call = next(
        c for c in h.session.calls if isinstance(c, SendMessage) and c.text == texts.SETUP_CHOOSE
    )
    assert {b.callback_data for row in call.reply_markup.inline_keyboard for b in row} == {
        f"pg:tz:{tz}" for tz in CONTENT.timezones.values()
    }
    await h.tap("pg:tz:UTC", user_id=USER)
    assert store.get_chat(CHAT) is None
    assert h.session.toasts()[-1] == texts.ADMIN_ONLY
    await h.tap("pg:tz:UTC", user_id=ADMIN)
    assert store.get_chat(CHAT).timezone == "UTC" and store.is_enabled(CHAT)


@pytest.mark.parametrize("first", ["to", "from"])
@pytest.mark.parametrize("addition_first", [False, True])
async def test_migration_suppresses_new_id_even_when_analytics_blocks_transfer(
    env, first, addition_first
):
    h, store, sleep, inv = env
    store.enable_chat(CHAT, NOON, "UTC")
    if addition_first:
        await h._feed(my_chat_member=payload(h, chat=NEW_CHAT))
    messages = [
        (CHAT, {"migrate_to_chat_id": NEW_CHAT}),
        (NEW_CHAT, {"migrate_from_chat_id": CHAT}),
    ]
    if first == "from":
        messages.reverse()
    for chat_id, fields in messages:
        await h.service(chat_id, **fields)
    if not addition_first:
        await h._feed(my_chat_member=payload(h, chat=NEW_CHAT))
    await release(sleep, inv)
    assert h.session.said() == []
    assert not inv.pending
    if addition_first:
        assert store.get_chat(NEW_CHAT) is None
        assert not store.is_enabled(CHAT)
    else:
        assert store.is_enabled(NEW_CHAT)


async def test_forbidden_background_send_logs_and_dispatch_still_works(env, caplog):
    h, _, sleep, inv = env
    original = h.session.make_request

    async def fail(bot, method, timeout=None):
        if isinstance(method, SendMessage) and method.text == texts.SETUP_CHOOSE:
            raise TelegramForbiddenError(method=method, message="removed")
        return await original(bot, method, timeout)

    h.session.make_request = fail
    await h._feed(my_chat_member=payload(h))
    await release(sleep, inv)
    assert "setup invitation failed" in caplog.text
    await h.command("/help")
    assert h.session.said() == [texts.help_text()]


async def test_router_shutdown_cancels_before_store_close(env):
    h, _, sleep, inv = env
    await h._feed(my_chat_member=payload(h))
    await asyncio.sleep(0)
    tasks = list(inv.tasks)
    await h.dp.emit_shutdown()
    assert all(t.done() for t in tasks)
    assert not inv.pending
    assert h.session.said() == []


async def test_manual_setup_during_delay_suppresses_invitation(env):
    h, store, sleep, inv = env
    await h._feed(my_chat_member=payload(h))
    await asyncio.sleep(0)
    await h.command("/setup Europe/Moscow")
    await release(sleep, inv)
    assert h.session.said() == [texts.SETUP_DONE.format(tz="Europe/Moscow")]
    assert store.get_chat(CHAT).timezone == "Europe/Moscow"


async def test_removal_update_cancels_pending_invitation(env):
    h, _, sleep, inv = env
    await h._feed(my_chat_member=payload(h))
    await asyncio.sleep(0)
    await h._feed(my_chat_member=payload(h, old="member", new="left"))
    await release(sleep, inv)
    assert h.session.said() == []
    assert not inv.pending
