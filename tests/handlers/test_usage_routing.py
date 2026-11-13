"""Real routing plus failure injection at SQLite and the Telegram session boundary."""

from datetime import timedelta

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage, SendPhoto
from aiogram.types import Chat, Message, User

from handlers.test_commands import ADMIN, CHAT, NEW_CHAT, NOON, USER, Harness
from pigeon.clock import FixedClock
from pigeon.repo.store import PigeonDeck, Store
from pigeon.repo.usage import read_report
from pigeon.services import game
from pigeon.services.usage import Usage
from pigeon.telegram.usage import install_delivery_usage


@pytest.fixture
async def env(tmp_path):
    path = tmp_path / "routing.db"
    store = Store(path)
    clock = FixedClock(NOON)
    h = Harness(store, clock)
    install_delivery_usage(h.bot, Usage(store.usage, clock))
    yield h, store, clock, path
    await h.bot.session.close()
    store.close()


def counts(path, clock, chat=CHAT):
    result = {}
    for row in read_report(path, clock.now())["days"]:
        if row["chat_id"] == chat:
            for key, n in row["metrics"].items():
                result[key] = result.get(key, 0) + n
    return result


async def test_commands_count_before_guards_and_successful_setup_only(env):
    h, store, clock, path = env
    await h.command("/arrive", user_id=USER)  # no setup
    await h.command("/setup bad-zone")
    await h.command("/setup UTC", user_id=USER)  # refused
    await h.command("/setup UTC")
    await h.command("/arrive", user_id=USER)  # no rights
    await h.command("/pause wrong")  # bad args
    await h.command("/unknown secret")
    await h.command("/help", chat_id=USER, chat_type="private")
    m = counts(path, clock)
    assert m["command.arrive"] == 2
    assert m["command.setup"] == 3 and m["setup.saved"] == 1
    assert m["command.pause"] == 1
    assert "command.help" not in m
    assert read_report(path, clock.now())["participants"] == {CHAT: 2}
    assert store.current_open_event(CHAT) is None


async def test_buttons_and_unique_users_include_refusals_not_foreign_events(env):
    h, store, clock, path = env
    store.enable_chat(CHAT, NOON, "UTC")
    store.enable_chat(NEW_CHAT, NOON, "UTC")
    event = game.arrive(store, clock, CHAT)
    await h.tap(f"pg:hit:{event.id}")
    await h.tap(f"pg:hit:{event.id}")  # repeated button does not inflate unique
    await h.tap("pg:tz:UTC", user_id=USER)  # permission refused, still activity
    await h.tap("pg:tz:UTC", user_id=ADMIN)
    await h.tap(f"pg:hit:{event.id}", user_id=77, chat_id=NEW_CHAT)
    await h.tap("pg:hit:not-an-id", user_id=78)
    await h.tap("pg:retired:1", user_id=79)
    assert read_report(path, clock.now())["participants"] == {CHAT: 2}
    assert counts(path, clock)["setup.saved"] == 1
    assert store.hit_count(event.id) == 1
    clock.set(event.closes_at + timedelta(seconds=1))
    await h.tap(f"pg:shoo:{event.id}", user_id=80)  # expired, belongs to this chat
    assert read_report(path, clock.now())["participants"] == {CHAT: 3}


async def test_anonymous_and_bot_commands_count_but_have_no_actor(env):
    h, store, clock, path = env
    await h.command("/help", sender_chat=Chat(id=CHAT, type="supergroup"))
    message = Message(
        message_id=100,
        date=NOON,
        chat=Chat(id=CHAT, type="supergroup"),
        from_user=User(id=3, is_bot=True, first_name="bot"),
        text="/help",
        entities=[{"type": "bot_command", "offset": 0, "length": 5}],
    )
    await h._feed(message=message)
    assert counts(path, clock)["command.help"] == 2
    assert read_report(path, clock.now())["participants"] == {}


async def test_analytics_write_failure_preserves_command_button_and_arrival(env, caplog):
    h, store, clock, path = env
    store._conn.execute(
        "CREATE TEMP TRIGGER deny_usage BEFORE INSERT ON usage_counters "
        "BEGIN SELECT RAISE(FAIL, 'usage unavailable'); END"
    )
    store._conn.execute(
        "CREATE TEMP TRIGGER deny_actor BEFORE INSERT ON usage_actors "
        "BEGIN SELECT RAISE(FAIL, 'usage unavailable'); END"
    )
    await h.command("/setup UTC")
    assert store.is_enabled(CHAT)
    store.save_pigeon_deck(CHAT, PigeonDeck(["normal"], None))
    await h.command("/arrive")
    event = store.current_open_event(CHAT)
    assert event is not None and event.message_id == 901
    await h.tap(f"pg:hit:{event.id}")
    assert store.hit_count(event.id) == 1 and store.balance(CHAT, USER) > 0
    assert h.session.toasts() and h.session.edits()
    assert "usage recording failed" in caplog.text


@pytest.mark.parametrize(
    "method_type", [SendMessage, EditMessageText, SendPhoto, AnswerCallbackQuery]
)
async def test_delivery_failures_record_each_attempt_and_preserve_exception(env, method_type):
    h, store, clock, path = env
    original = h.session.make_request

    async def fail(bot, method, timeout=None):
        if isinstance(method, method_type):
            raise TelegramNetworkError(method=method, message="transport unavailable")
        return await original(bot, method, timeout)

    h.session.make_request = fail
    if method_type is AnswerCallbackQuery:
        for _ in range(2):
            with pytest.raises(TelegramNetworkError):
                await h.tap("pg:tz:UTC")
        assert counts(path, clock)["delivery.callback_error"] == 2
        # Callback context must not survive this dispatch.
        with pytest.raises(TelegramNetworkError):
            await h.bot.answer_callback_query("other")
        assert counts(path, clock)["delivery.callback_error"] == 2
    else:
        method = {
            SendMessage: SendMessage(chat_id=CHAT, text="secret"),
            EditMessageText: EditMessageText(chat_id=CHAT, message_id=1, text="secret"),
            SendPhoto: SendPhoto(chat_id=CHAT, photo="test"),
        }[method_type]
        for _ in range(2):
            with pytest.raises(TelegramNetworkError):
                await h.bot(method)
        assert counts(path, clock)["delivery.message_error"] == 2


async def test_not_modified_and_private_failures_are_not_metrics(env):
    h, store, clock, path = env

    async def fail(bot, method, timeout=None):
        raise TelegramBadRequest(method=method, message="Bad Request: message is not modified")

    h.session.make_request = fail
    with pytest.raises(TelegramBadRequest):
        await h.bot.edit_message_text(chat_id=CHAT, message_id=1, text="unchanged")
    assert "delivery.message_error" not in counts(path, clock)


async def test_membership_transitions_and_setup_button_before_chat_exists(env):
    h, store, clock, path = env
    user = {"id": h.bot.id, "is_bot": True, "first_name": "bot"}
    for old, new in (("left", "member"), ("member", "left"), ("left", "kicked")):

        def member(status):
            result = {"status": status, "user": user}
            if status == "kicked":
                result["until_date"] = 0
            return result

        await h._feed(
            my_chat_member={
                "chat": {"id": CHAT, "type": "supergroup"},
                "from": {"id": ADMIN, "is_bot": False, "first_name": "admin"},
                "date": int(NOON.timestamp()),
                "old_chat_member": member(old),
                "new_chat_member": member(new),
            }
        )
    assert counts(path, clock)["membership.added"] == 1
    assert counts(path, clock)["membership.removed"] == 1
    assert not store.is_enabled(CHAT)
    await h.tap("pg:tz:UTC", user_id=ADMIN)
    assert store.is_enabled(CHAT) and counts(path, clock)["setup.saved"] == 1


async def test_migration_service_messages_move_analytics_without_setup(env):
    h, store, clock, path = env
    await h.command("/help")
    assert store.get_chat(CHAT) is None
    await h.service(CHAT, migrate_to_chat_id=NEW_CHAT)
    await h.service(NEW_CHAT, migrate_from_chat_id=CHAT)
    assert read_report(path, clock.now())["participants"] == {NEW_CHAT: 1}
    assert store.get_chat(NEW_CHAT) is None
