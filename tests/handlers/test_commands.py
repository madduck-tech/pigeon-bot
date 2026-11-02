"""Real aiogram routing: updates go through the Dispatcher, API calls stop at a fake session."""

import random
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, GetChatMember, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from pigeon.clock import FixedClock
from pigeon.content import load_content
from pigeon.handlers.pigeon import build_router
from pigeon.render import texts
from pigeon.repo.store import Store
from pigeon.services import achievements, game

CHAT = -1001
NEW_CHAT = -100777
ADMIN = 1
USER = 2
NOON = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
CONTENT = load_content(Path(__file__).resolve().parents[2] / "content")


class Rolls(random.Random):
    """Every random() is this roll, so a test decides whether the pigeon flies."""

    roll = 0.99

    def random(self) -> float:
        return self.roll


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls = []

    async def close(self) -> None:
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        chat = Chat(id=CHAT, type="supergroup")
        if isinstance(method, GetChatMember):
            return SimpleNamespace(status="administrator" if method.user_id == ADMIN else "member")
        if isinstance(method, SendMessage):
            return Message(message_id=901, date=NOON, chat=chat, text=method.text)
        return True

    async def stream_content(self, *args, **kwargs) -> AsyncGenerator[bytes, None]:
        yield b""

    def said(self) -> list[str]:
        return [c.text for c in self.calls if isinstance(c, SendMessage)]

    def toasts(self) -> list[str]:
        return [c.text for c in self.calls if isinstance(c, AnswerCallbackQuery)]

    def edits(self) -> list[EditMessageText]:
        return [c for c in self.calls if isinstance(c, EditMessageText)]


class Harness:
    def __init__(self, store: Store, clock: FixedClock) -> None:
        self.store = store
        self.clock = clock
        self.session = FakeSession()
        self.bot = Bot("42:TEST", session=self.session)
        self.dp = Dispatcher()
        self.rng = Rolls(1)
        self.dp.include_router(build_router(store, clock, CONTENT, self.rng))
        self.update_id = 0

    async def _feed(self, **kwargs) -> None:
        self.update_id += 1
        raw = Update(update_id=self.update_id, **kwargs)
        # Parse from JSON as polling does: that is what binds the bot to nested objects.
        payload = raw.model_dump(mode="json", exclude_none=True, by_alias=True)
        update = Update.model_validate(payload, context={"bot": self.bot})
        await self.dp.feed_update(self.bot, update)

    async def command(
        self,
        text: str,
        user_id: int = ADMIN,
        first_name: str = "Anna",
        chat_id: int = CHAT,
        sender_chat: Chat | None = None,
    ) -> None:
        message = Message(
            message_id=self.update_id + 1,
            date=NOON,
            chat=Chat(id=chat_id, type="supergroup"),
            from_user=User(id=user_id, is_bot=False, first_name=first_name),
            sender_chat=sender_chat,
            text=text,
            entities=[
                {"type": "bot_command", "offset": 0, "length": len(text.split()[0])},
            ],
        )
        await self._feed(message=message)

    async def tap(
        self, data: str, user_id: int = USER, first_name: str = "Boris", chat_id: int = CHAT
    ) -> None:
        query = CallbackQuery(
            id="1",
            from_user=User(id=user_id, is_bot=False, first_name=first_name),
            chat_instance="c",
            data=data,
            message=Message(
                message_id=500,
                date=NOON,
                chat=Chat(id=chat_id, type="supergroup"),
                text="x",
            ),
        )
        await self._feed(callback_query=query)

    async def service(self, chat_id: int, **fields) -> None:
        message = Message(
            message_id=self.update_id + 1,
            date=NOON,
            chat=Chat(id=chat_id, type="supergroup"),
            **fields,
        )
        await self._feed(message=message)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "pigeon.db")
    s.enable_chat(CHAT, NOON, "Europe/Moscow")
    yield s
    s.close()


@pytest.fixture
def clock():
    return FixedClock(NOON)


@pytest.fixture
async def harness(store, clock):
    h = Harness(store, clock)
    yield h
    await h.bot.session.close()


VISITOR = game.Visitor("Гена", "Голубь {name} прилетел, курлык.")


async def test_pause_is_for_admins_only(harness, store) -> None:
    await harness.command("/pigeon pause 24h", user_id=USER)
    assert harness.session.said() == [texts.ADMIN_ONLY]
    assert store.get_chat(CHAT).paused_until is None


async def test_pause_for_a_day_and_off(harness, store, clock) -> None:
    await harness.command("/pigeon pause 24h")
    assert store.get_chat(CHAT).paused_until == clock.now() + timedelta(hours=24)
    await harness.command("/pigeon pause 7d")
    assert store.get_chat(CHAT).paused_until == clock.now() + timedelta(days=7)
    await harness.command("/pigeon pause off")
    assert store.get_chat(CHAT).paused_until is None
    assert harness.session.said()[-1] == texts.PAUSE_OFF


async def test_pause_rejects_other_arguments(harness, store) -> None:
    await harness.command("/pigeon pause 3w")
    assert harness.session.said() == [texts.PAUSE_BAD]
    assert store.get_chat(CHAT).paused_until is None


async def test_fortune_once_a_day_as_a_reply(harness) -> None:
    await harness.command("/pigeon fortune", user_id=USER)
    first = harness.session.calls[-1]
    assert first.text in {texts.fortune(line) for line in CONTENT.fortunes}
    assert first.reply_parameters is not None or first.reply_to_message_id is not None
    await harness.command("/pigeon fortune", user_id=USER)
    assert harness.session.said()[-1] == texts.FORTUNE_ALREADY


async def test_profile_shows_points_achievements_and_trophy(harness, store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, event.id, ADMIN, "Anna")
    store.award(CHAT, ADMIN, achievements.SNIPER, clock.now())
    clock.set(clock.now() + timedelta(days=2))
    await harness.command("/pigeon profile")
    text = harness.session.said()[-1]
    assert "Очки: <b>1</b>" in text and "Снайпер" in text
    assert "Золотой плевок: 2 дн." in text


async def test_profile_of_a_newcomer(harness) -> None:
    await harness.command("/pigeon profile", user_id=USER)
    text = harness.session.said()[-1]
    assert "Очки: <b>0</b>" in text and "пока нет" in text


async def test_profile_survives_achievements_of_closed_mechanics(harness, store, clock) -> None:
    # Regression: old databases hold `bullseye`, which has no text any more.
    store.award(CHAT, ADMIN, "bullseye", clock.now())
    store.award(CHAT, ADMIN, achievements.SNIPER, clock.now())
    await harness.command("/pigeon profile")
    text = harness.session.said()[-1]
    assert "Снайпер" in text and "bullseye" not in text


async def test_stats_show_the_trophy_and_rating(harness, store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, event.id, ADMIN, "Anna")
    clock.set(clock.now() + timedelta(days=3))
    await harness.command("/pigeon stats")
    text = harness.session.said()[-1]
    assert "Золотой плевок: <b>Anna</b> (держит 3 дн.)" in text
    assert "1. Anna — 1" in text


async def test_stats_in_a_new_chat(harness) -> None:
    await harness.command("/pigeon stats")
    assert "пока никто не попадал" in harness.session.said()[-1]


async def test_arrive_is_for_admins_only(harness, store) -> None:
    await harness.command("/pigeon arrive", user_id=USER)
    assert harness.session.said() == [texts.ADMIN_ONLY]
    assert store.current_open_event(CHAT) is None


async def test_arrive_releases_a_named_pigeon_with_two_buttons(harness, store) -> None:
    await harness.command("/pigeon arrive")
    event = store.current_open_event(CHAT)
    assert event.pigeon_name in CONTENT.names and event.intro in CONTENT.arrivals
    call = harness.session.calls[-1]
    assert f"<b>{event.pigeon_name}</b>" in call.text
    row = call.reply_markup.inline_keyboard[0]
    assert [button.callback_data for button in row] == [
        f"pg:hit:{event.id}",
        f"pg:shoo:{event.id}",
    ]
    assert store.get_event(event.id).message_id == 901


async def test_status_names_the_pigeon_on_the_windowsill(harness, store, clock) -> None:
    game.arrive(store, clock, CHAT, VISITOR)
    await harness.command("/pigeon")
    assert harness.session.said() == [texts.status_sitting("Гена", 0)]


async def test_status_with_no_pigeon(harness) -> None:
    await harness.command("/pigeon")
    assert harness.session.said() == [texts.STATUS_AWAY]


async def test_status_of_an_arrival_from_before_the_dovecote(harness, store, clock) -> None:
    now = clock.now()
    store.open_event(CHAT, now, now + timedelta(hours=1))
    await harness.command("/pigeon")
    assert harness.session.said() == [texts.status_sitting(texts.PIGEON_NAME, 0)]


async def test_hit_updates_the_count_and_keeps_the_phrase_and_buttons(harness, store, clock):
    event = game.arrive(store, clock, CHAT, VISITOR)
    await harness.tap(f"pg:hit:{event.id}")
    assert harness.session.toasts() == [texts.HIT_OK]
    edit = harness.session.edits()[-1]
    assert "Голубь <b>Гена</b> прилетел, курлык." in edit.text
    assert "Попаданий: <b>1</b>" in edit.text
    assert edit.reply_markup is not None


async def test_hit_button_announces_an_achievement_in_the_toast(harness, store, clock) -> None:
    for _ in range(4):
        event = game.arrive(store, clock, CHAT)
        await harness.tap(f"pg:hit:{event.id}")
        clock.set(event.closes_at)
    event = game.arrive(store, clock, CHAT)
    await harness.tap(f"pg:hit:{event.id}")
    toast = harness.session.toasts()[-1]
    assert "Снайпер" in toast and "Ранняя пташка" in toast
    assert len(toast) <= 200  # Telegram's limit for callback answers


async def test_shoo_that_fails_is_logged_in_the_message(harness, store, clock) -> None:
    event = game.arrive(store, clock, CHAT, VISITOR)
    await harness.tap(f"pg:shoo:{event.id}")
    assert harness.session.toasts() == [texts.SHOO_STAYED]
    assert store.current_open_event(CHAT) is not None
    edit = harness.session.edits()[-1]
    assert "<b>Boris</b>:" in edit.text
    assert any(line in edit.text for line in CONTENT.shoos.stayed)
    assert edit.reply_markup is not None
    assert harness.session.said() == []  # no new messages in the chat


async def test_shoo_that_works_ends_the_arrival(harness, store, clock) -> None:
    event = game.arrive(store, clock, CHAT, VISITOR)
    game.hit(store, clock, CHAT, event.id, ADMIN, "Anna")
    harness.rng.roll = 0.0
    await harness.tap(f"pg:shoo:{event.id}")
    assert harness.session.toasts() == [texts.SHOO_FLEW]
    assert store.current_open_event(CHAT) is None
    edit = harness.session.edits()[-1]
    assert "Голубь <b>Гена</b> улетел." in edit.text
    assert "Попаданий: <b>1</b>" in edit.text
    assert "Кто спугнул: <b>Boris</b>" in edit.text
    assert edit.reply_markup is None


async def test_second_shoo_by_the_same_person_is_refused(harness, store, clock) -> None:
    event = game.arrive(store, clock, CHAT, VISITOR)
    await harness.tap(f"pg:shoo:{event.id}")
    await harness.tap(f"pg:shoo:{event.id}")
    assert harness.session.toasts()[-1] == texts.SHOO_ALREADY
    assert store.shoo_count(CHAT, event.id) == 1


async def test_buttons_of_a_flown_pigeon_do_nothing(harness, store, clock) -> None:
    event = game.arrive(store, clock, CHAT, VISITOR)
    harness.rng.roll = 0.0
    await harness.tap(f"pg:shoo:{event.id}")
    await harness.tap(f"pg:hit:{event.id}", user_id=ADMIN)
    await harness.tap(f"pg:shoo:{event.id}", user_id=ADMIN)
    assert harness.session.toasts()[-2:] == [texts.HIT_CLOSED, texts.HIT_CLOSED]
    assert store.balance(CHAT, ADMIN) == 0


async def test_fifth_scared_pigeon_announces_the_achievement(harness, store, clock) -> None:
    harness.rng.roll = 0.0
    for _ in range(5):
        event = game.arrive(store, clock, CHAT, VISITOR)
        await harness.tap(f"pg:shoo:{event.id}")
    assert "Гроза голубей" in harness.session.toasts()[-1]


async def test_shoo_failing_to_edit_because_nothing_changed_is_fine(harness, store, clock) -> None:
    event = game.arrive(store, clock, CHAT, VISITOR)
    real = harness.session.make_request

    async def unchanged(bot, method, timeout=None):
        if isinstance(method, EditMessageText):
            raise TelegramBadRequest(method, "message is not modified")
        return await real(bot, method, timeout)

    harness.session.make_request = unchanged
    await harness.tap(f"pg:shoo:{event.id}")
    assert harness.session.toasts() == [texts.SHOO_STAYED]


@pytest.mark.parametrize("data", ["pg:dart:1", "pg:bet:1:ge3", "pg:duel_ok:1", "pg:duel_no:1"])
async def test_buttons_of_closed_mechanics_answer_instead_of_spinning(harness, data) -> None:
    await harness.tap(data)
    assert harness.session.toasts() == [texts.BUTTON_RETIRED]


@pytest.mark.parametrize("command", ["duel", "photo", "name"])
async def test_closed_commands_fall_back_to_the_status(harness, command) -> None:
    await harness.command(f"/pigeon {command}")
    assert harness.session.said() == [texts.STATUS_AWAY]


async def test_chat_messages_are_not_read_or_stored(harness, store) -> None:
    marker = "голубь-секретноесообщение"
    await harness.service(CHAT, text=marker)
    assert harness.session.calls == []
    assert marker not in "\n".join(store._conn.iterdump())


async def test_migration_service_message_moves_the_chat(harness, store) -> None:
    await harness.service(CHAT, migrate_to_chat_id=NEW_CHAT)
    assert store.get_chat(CHAT) is None
    assert store.is_enabled(NEW_CHAT)


async def test_second_migration_notice_changes_nothing(harness, store) -> None:
    await harness.service(CHAT, migrate_to_chat_id=NEW_CHAT)
    await harness.service(NEW_CHAT, migrate_from_chat_id=CHAT)
    assert store.is_enabled(NEW_CHAT)


async def test_migration_notice_from_the_new_chat_alone_is_enough(harness, store) -> None:
    await harness.service(NEW_CHAT, migrate_from_chat_id=CHAT)
    assert store.is_enabled(NEW_CHAT)
    assert store.get_chat(CHAT) is None


@pytest.mark.parametrize("name", ["Europe", "America", "Etc", "no/such"])
async def test_setup_rejects_a_timezone_folder_without_crashing(harness, name) -> None:
    # Regression: on Python 3.12 "Europe" raised IsADirectoryError and the bot said nothing.
    await harness.command(f"/pigeon setup {name}")
    assert harness.session.said() == [texts.TZ_BAD]


def _timezone_buttons(call) -> list[str]:
    return [b.callback_data for row in call.reply_markup.inline_keyboard for b in row]


async def test_setup_of_a_new_chat_waits_for_a_timezone_choice(harness, store) -> None:
    await harness.command("/pigeon setup", chat_id=NEW_CHAT)
    call = harness.session.calls[-1]
    assert call.text == texts.SETUP_CHOOSE
    assert "pg:tz:Europe/Moscow" in _timezone_buttons(call)
    assert store.get_chat(NEW_CHAT) is None  # silently starting in UTC would fly at night

    await harness.tap("pg:tz:Asia/Omsk", user_id=ADMIN, chat_id=NEW_CHAT)
    assert store.is_enabled(NEW_CHAT)
    assert store.get_chat(NEW_CHAT).timezone == "Asia/Omsk"
    edit = harness.session.edits()[-1]
    assert edit.text == texts.SETUP_DONE.format(tz="Asia/Omsk") and edit.reply_markup is None


async def test_repeating_setup_shows_the_current_zone_and_changes_it(harness, store) -> None:
    await harness.command("/pigeon setup")
    assert harness.session.said() == [texts.SETUP_CHOOSE_CURRENT.format(tz="Europe/Moscow")]
    assert store.get_chat(CHAT).timezone == "Europe/Moscow"
    await harness.tap("pg:tz:Europe/London", user_id=ADMIN)
    assert store.get_chat(CHAT).timezone == "Europe/London"


async def test_setup_with_a_timezone_needs_no_buttons(harness, store) -> None:
    await harness.command("/pigeon setup Asia/Tokyo")
    assert harness.session.said() == [texts.SETUP_DONE.format(tz="Asia/Tokyo")]
    assert store.get_chat(CHAT).timezone == "Asia/Tokyo"


async def test_setup_is_for_admins_only(harness) -> None:
    await harness.command("/pigeon setup", user_id=USER)
    assert harness.session.said() == [texts.ADMIN_ONLY]


async def test_only_an_admin_can_choose_a_timezone(harness, store) -> None:
    await harness.tap("pg:tz:Asia/Omsk", user_id=USER)
    assert harness.session.toasts() == [texts.ADMIN_ONLY]
    assert store.get_chat(CHAT).timezone == "Europe/Moscow"


async def test_forged_timezone_button_is_refused(harness, store) -> None:
    await harness.tap("pg:tz:Asia/Tokyo", user_id=ADMIN)  # valid, but not offered
    assert harness.session.toasts() == [texts.TZ_UNKNOWN]
    assert store.get_chat(CHAT).timezone == "Europe/Moscow"


async def test_double_tap_on_a_timezone_is_harmless(harness, store) -> None:
    real = harness.session.make_request
    await harness.tap("pg:tz:Asia/Omsk", user_id=ADMIN)

    async def unchanged(bot, method, timeout=None):
        if isinstance(method, EditMessageText):
            raise TelegramBadRequest(method, "message is not modified")
        return await real(bot, method, timeout)

    harness.session.make_request = unchanged
    await harness.tap("pg:tz:Asia/Omsk", user_id=ADMIN)
    assert store.get_chat(CHAT).timezone == "Asia/Omsk"


async def test_anonymous_admin_can_run_setup(harness, store) -> None:
    # Regression: the service account is a plain member, so anonymous admins were refused.
    await harness.command(
        "/pigeon setup Asia/Omsk",
        user_id=1087968824,
        sender_chat=Chat(id=CHAT, type="supergroup"),
    )
    assert harness.session.said() == [texts.SETUP_DONE.format(tz="Asia/Omsk")]


async def test_a_channel_posting_in_the_chat_is_not_an_admin(harness, store) -> None:
    await harness.command(
        "/pigeon setup Asia/Omsk",
        user_id=136817688,
        sender_chat=Chat(id=-100555, type="channel"),
    )
    assert harness.session.said() == [texts.ADMIN_ONLY]


async def test_help_says_what_is_kept_and_lists_only_live_commands(harness) -> None:
    await harness.command("/pigeon help")
    text = harness.session.said()[0]
    assert "Голубятня" in text and "не сохраняет" in text
    assert all(word in text for word in ("/pigeon stats", "/pigeon fortune", "/pigeon arrive"))
    assert not any(word in text for word in ("/pigeon duel", "/pigeon photo", "/pigeon name"))


async def test_callback_data_fits_telegram() -> None:
    from pigeon.telegram.messages import arrival_keyboard, timezone_keyboard

    row = arrival_keyboard(2**63 - 1).inline_keyboard[0]
    assert all(len(button.callback_data.encode()) <= 64 for button in row)
    assert all(
        len(data.encode()) <= 64
        for data in _timezone_buttons(
            SimpleNamespace(reply_markup=timezone_keyboard(CONTENT.timezones))
        )
    )
