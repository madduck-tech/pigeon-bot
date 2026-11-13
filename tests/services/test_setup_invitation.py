"""Deterministic addition, delay, deduplication and lifecycle rules from #14."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from pigeon.repo.store import Store
from pigeon.services.setup_invitation import SetupInvitations

NOW = datetime(2026, 11, 13, tzinfo=UTC)


class Sleep:
    def __init__(self):
        self.seconds = []
        self.ready = asyncio.Event()

    async def __call__(self, seconds):
        self.seconds.append(seconds)
        await self.ready.wait()


@pytest.fixture
async def env():
    store = Store(":memory:")
    sleep = Sleep()
    invitations = SetupInvitations(store, sleep)
    send = AsyncMock()
    yield store, sleep, invitations, send
    await invitations.close()
    store.close()


def add(inv, send, *, chat=-1, kind="group", old="left", new="member", ident=1):
    inv.membership(chat, kind, old, new, ident, send)


async def release(sleep, inv):
    sleep.ready.set()
    await asyncio.gather(*list(inv.tasks), return_exceptions=True)


@pytest.mark.parametrize("kind", ["group", "supergroup"])
@pytest.mark.parametrize("old", ["left", "kicked"])
@pytest.mark.parametrize("new", ["member", "administrator"])
async def test_addition_delays_once_without_enabling_or_budget(env, kind, old, new):
    store, sleep, inv, send = env
    add(inv, send, kind=kind, old=old, new=new)
    await asyncio.sleep(0)
    assert sleep.seconds == [5]
    send.assert_not_awaited()
    assert store.get_chat(-1) is None
    await release(sleep, inv)
    send.assert_awaited_once()
    assert store.get_chat(-1) is None
    assert store.count_auto_events_since(-1, NOW) == 0
    # Repeat delivery after completion must not send a second invitation.
    add(inv, send, kind=kind, old=old, new=new)
    assert not inv.pending
    send.assert_awaited_once()


@pytest.mark.parametrize("kind", ["private", "channel"])
async def test_other_chat_types(env, kind):
    _, _, inv, send = env
    add(inv, send, kind=kind)
    assert not inv.tasks


@pytest.mark.parametrize(
    "old,new",
    [
        ("member", "administrator"),
        ("administrator", "member"),
        ("member", "member"),
        ("restricted", "member"),
        ("left", "restricted"),
        ("left", "kicked"),
        ("administrator", "left"),
    ],
)
async def test_other_transitions(env, old, new):
    _, _, inv, send = env
    add(inv, send, old=old, new=new)
    assert not inv.tasks


@pytest.mark.parametrize("disabled", [False, True])
@pytest.mark.parametrize("during_delay", [False, True])
async def test_configured_chat_suppresses_invitation(env, disabled, during_delay):
    store, sleep, inv, send = env
    if during_delay:
        add(inv, send)
        await asyncio.sleep(0)
    store.enable_chat(-1, NOW, "UTC")
    if disabled:
        store.disable_chat(-1)
    if not during_delay:
        add(inv, send)
    await release(sleep, inv)
    send.assert_not_awaited()


async def test_analytics_alone_does_not_suppress(env):
    store, sleep, inv, send = env
    store.usage.record(-1, NOW, "membership.added")
    add(inv, send)
    await release(sleep, inv)
    send.assert_awaited_once()


async def test_duplicate_pending_and_independent_chats(env):
    _, sleep, inv, send = env
    add(inv, send)
    add(inv, send, ident=2)
    other = AsyncMock()
    add(inv, other, chat=-2, ident=3)
    await asyncio.sleep(0)
    assert sleep.seconds == [5, 5]
    await release(sleep, inv)
    send.assert_awaited_once()
    other.assert_awaited_once()


async def test_removal_then_readd_does_not_let_old_task_remove_new(env):
    _, sleep, inv, send = env
    add(inv, send)
    await asyncio.sleep(0)
    add(inv, send, old="member", new="left", ident=2)
    add(inv, send, ident=3)
    await asyncio.sleep(0)
    assert -1 in inv.pending
    await release(sleep, inv)
    send.assert_awaited_once()


@pytest.mark.parametrize("migration_first", [False, True])
async def test_migration_cancels_both_ids_and_blocks_later_add(env, migration_first):
    _, sleep, inv, send = env
    if migration_first:
        inv.migration(-1, -2)
    add(inv, send)
    add(inv, send, chat=-2, ident=2)
    await asyncio.sleep(0)
    inv.migration(-1, -2)
    await release(sleep, inv)
    send.assert_not_awaited()
    assert not inv.pending


async def test_shutdown_awaits_cancellation_and_refuses_new_work(env):
    _, _, inv, send = env
    add(inv, send)
    await asyncio.sleep(0)
    tasks = list(inv.tasks)
    await inv.close()
    assert all(task.done() for task in tasks)
    add(inv, send, chat=-2, ident=2)
    assert not inv.pending
    send.assert_not_awaited()


async def test_send_error_logged_no_retry_and_other_chat_survives(env, caplog):
    _, sleep, inv, send = env
    send.side_effect = RuntimeError("cannot write")
    add(inv, send)
    other = AsyncMock()
    add(inv, other, chat=-2, ident=2)
    await release(sleep, inv)
    assert "setup invitation failed" in caplog.text
    assert not inv.pending
    send.assert_awaited_once()
    other.assert_awaited_once()


async def test_real_removal_and_readdition_after_migration_can_invite(env):
    _, sleep, inv, send = env
    inv.migration(-1, -2)
    add(inv, send, chat=-2, ident=1)
    assert not inv.pending
    add(inv, send, chat=-2, old="member", new="left", ident=2)
    add(inv, send, chat=-2, ident=3)
    await release(sleep, inv)
    send.assert_awaited_once()


async def test_update_ids_may_reset_after_idle_week(env):
    _, sleep, inv, send = env
    add(inv, send, ident=10000)
    await release(sleep, inv)
    add(inv, send, old="member", new="left", ident=10001)
    add(inv, send, ident=100)
    await release(sleep, inv)
    assert send.await_count == 2


async def test_reused_id_on_later_date_is_a_new_event(env):
    _, sleep, inv, send = env
    inv.membership(-1, "group", "left", "member", 1, send, event_date=100)
    await release(sleep, inv)
    inv.membership(-1, "group", "member", "left", 2, send, event_date=200)
    inv.membership(-1, "group", "left", "member", 1, send, event_date=700000)
    await release(sleep, inv)
    assert send.await_count == 2
