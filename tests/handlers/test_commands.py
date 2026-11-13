"""Real aiogram routing: updates go through the Dispatcher, API calls stop at a fake session."""

import asyncio
import json
import random
import re
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    GetChatMember,
    GetMe,
    SendMessage,
    SendPhoto,
)
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from pigeon.clock import FixedClock
from pigeon.content import load_content
from pigeon.handlers.pigeon import build_router
from pigeon.render import texts
from pigeon.repo.store import PigeonDeck, Store
from pigeon.services import achievements, adult, game
from pigeon.services.oboobs import GiftImage

CHAT = -1001
NEW_CHAT = -100777
ADMIN = 1
USER = 2
BOT_USERNAME = "golubinskity_bot"
NOON = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
CONTENT = load_content(Path(__file__).resolve().parents[2] / "content")


class Rolls(random.Random):
    """Every random() is this roll, so a test decides whether the pigeon flies."""

    roll = 0.99

    def random(self) -> float:
        return self.roll


class FakeGiftProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    async def random_image(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("source unavailable")
        return GiftImage(b"jpeg", "gift.jpg")


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls = []
        self.fail_photos = False

    async def close(self) -> None:
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        chat = Chat(id=CHAT, type="supergroup")
        if isinstance(method, GetMe):
            return User(id=42, is_bot=True, first_name="Голубятня", username=BOT_USERNAME)
        if isinstance(method, GetChatMember):
            return SimpleNamespace(status="administrator" if method.user_id == ADMIN else "member")
        if isinstance(method, SendPhoto):
            if self.fail_photos:
                raise RuntimeError("telegram unavailable")
            return Message(message_id=902, date=NOON, chat=chat)
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

    def photos(self) -> list[SendPhoto]:
        return [c for c in self.calls if isinstance(c, SendPhoto)]


class Harness:
    def __init__(self, store: Store, clock: FixedClock, chronicle_enabled: bool = False) -> None:
        self.store = store
        self.clock = clock
        self.session = FakeSession()
        self.bot = Bot("42:TEST", session=self.session)
        self.dp = Dispatcher()
        self.rng = Rolls(1)
        self.gifts = FakeGiftProvider()
        self.dp.include_router(
            build_router(
                store,
                clock,
                CONTENT,
                self.rng,
                gift_provider=self.gifts,
                chronicle_enabled=chronicle_enabled,
            )
        )
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
        chat_type: str = "supergroup",
    ) -> None:
        message = Message(
            message_id=self.update_id + 1,
            date=NOON,
            chat=Chat(id=chat_id, type=chat_type),
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


async def test_help_names_the_chronicle_service_only_when_it_is_on(store, clock) -> None:
    for enabled in (False, True):
        h = Harness(store, clock, chronicle_enabled=enabled)
        await h.command("/help")
        said = h.session.said()[-1]
        assert "Приватность" in said
        assert ("OpenCode Go" in said) is enabled
        if enabled:
            assert "имена игроков" in said
        await h.bot.session.close()


VISITOR = game.Visitor("Гена", "Голубь {name} прилетел, курлык.")


async def test_pause_is_for_admins_only(harness, store) -> None:
    await harness.command("/pause 24h", user_id=USER)
    assert harness.session.said() == [texts.ADMIN_ONLY]
    assert store.get_chat(CHAT).paused_until is None


async def test_pause_for_a_day_and_off(harness, store, clock) -> None:
    await harness.command("/pause 24h")
    assert store.get_chat(CHAT).paused_until == clock.now() + timedelta(hours=24)
    await harness.command("/pause 7d")
    assert store.get_chat(CHAT).paused_until == clock.now() + timedelta(days=7)
    await harness.command("/pause off")
    assert store.get_chat(CHAT).paused_until is None
    assert harness.session.said()[-1] == texts.PAUSE_OFF


async def test_pause_rejects_other_arguments(harness, store) -> None:
    await harness.command("/pause 3w")
    assert harness.session.said() == [texts.PAUSE_BAD]
    assert store.get_chat(CHAT).paused_until is None


async def test_fortune_once_a_day_as_a_reply(harness) -> None:
    await harness.command("/fortune", user_id=USER)
    first = harness.session.calls[-1]
    assert first.text in {
        texts.render_fortune(line, name)
        for line in CONTENT.fortunes
        if line.format != "serial"
        for name in CONTENT.names
    }
    assert first.reply_parameters is not None or first.reply_to_message_id is not None
    await harness.command("/fortune", user_id=USER)
    assert harness.session.said()[-1] == texts.FORTUNE_ALREADY


async def test_profile_shows_points_achievements_and_trophy(harness, store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, event.id, ADMIN, "Anna")
    store.award(CHAT, ADMIN, achievements.SNIPER, clock.now())
    clock.set(clock.now() + timedelta(days=2))
    await harness.command("/profile")
    text = harness.session.said()[-1]
    assert "Очки: <b>1</b>" in text and "Снайпер" in text
    assert "Золотой плевок: 2 дн." in text


async def test_profile_of_a_newcomer(harness) -> None:
    await harness.command("/profile", user_id=USER)
    text = harness.session.said()[-1]
    assert "Очки: <b>0</b>" in text and "пока нет" in text


async def test_profile_survives_achievements_of_closed_mechanics(harness, store, clock) -> None:
    # Regression: old databases hold `bullseye`, which has no text any more.
    store.award(CHAT, ADMIN, "bullseye", clock.now())
    store.award(CHAT, ADMIN, achievements.SNIPER, clock.now())
    await harness.command("/profile")
    text = harness.session.said()[-1]
    assert "Снайпер" in text and "bullseye" not in text


async def test_stats_show_the_trophy_and_rating(harness, store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, event.id, ADMIN, "Anna")
    clock.set(clock.now() + timedelta(days=3))
    await harness.command("/stats all")
    text = harness.session.said()[-1]
    assert "Золотой плевок: <b>Anna</b> (держит 3 дн.)" in text
    assert "1. Anna — 1" in text


async def test_stats_in_a_new_chat(harness) -> None:
    await harness.command("/stats all")
    assert "пока никто не попадал" in harness.session.said()[-1]


async def test_arrive_is_for_admins_only(harness, store) -> None:
    await harness.command("/arrive", user_id=USER)
    assert harness.session.said() == [texts.ADMIN_ONLY]
    assert store.current_open_event(CHAT) is None


async def test_arrive_releases_a_named_pigeon_with_two_buttons(harness, store) -> None:
    await harness.command("/arrive")
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
    assert "+3" in harness.session.toasts()[0]
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
    await harness.command(f"/setup {name}")
    assert harness.session.said() == [texts.TZ_BAD]


def _timezone_buttons(call) -> list[str]:
    return [b.callback_data for row in call.reply_markup.inline_keyboard for b in row]


async def test_setup_of_a_new_chat_waits_for_a_timezone_choice(harness, store) -> None:
    await harness.command("/setup", chat_id=NEW_CHAT)
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
    await harness.command("/setup")
    assert harness.session.said() == [texts.SETUP_CHOOSE_CURRENT.format(tz="Europe/Moscow")]
    assert store.get_chat(CHAT).timezone == "Europe/Moscow"
    await harness.tap("pg:tz:Europe/London", user_id=ADMIN)
    assert store.get_chat(CHAT).timezone == "Europe/London"


async def test_setup_with_a_timezone_needs_no_buttons(harness, store) -> None:
    await harness.command("/setup Asia/Tokyo")
    assert harness.session.said() == [texts.SETUP_DONE.format(tz="Asia/Tokyo")]
    assert store.get_chat(CHAT).timezone == "Asia/Tokyo"


async def test_setup_is_for_admins_only(harness) -> None:
    await harness.command("/setup", user_id=USER)
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
        "/setup Asia/Omsk",
        user_id=1087968824,
        sender_chat=Chat(id=CHAT, type="supergroup"),
    )
    assert harness.session.said() == [texts.SETUP_DONE.format(tz="Asia/Omsk")]


async def test_a_channel_posting_in_the_chat_is_not_an_admin(harness, store) -> None:
    await harness.command(
        "/setup Asia/Omsk",
        user_id=136817688,
        sender_chat=Chat(id=-100555, type="channel"),
    )
    assert harness.session.said() == [texts.ADMIN_ONLY]


async def test_help_says_what_is_kept_and_lists_only_live_commands(harness) -> None:
    await harness.command("/help")
    text = harness.session.said()[0]
    assert "Голубятня" in text and "не сохраняет" in text
    commands = [name for name, _ in texts.COMMON_COMMANDS + texts.ADMIN_COMMANDS]
    assert all(f"/{name}" in text for name in commands)
    assert "@golubinskity_bot" in text
    assert re.search(r"/pigeon [a-z]", text) is None  # the retired `/pigeon <sub>` syntax
    assert not any(word in text for word in ("duel", "photo", "/name"))


async def test_callback_data_fits_telegram() -> None:
    from pigeon.telegram.messages import arrival_keyboard, gift_keyboard, timezone_keyboard

    event = SimpleNamespace(id=2**63 - 1, pigeon_type="normal")
    row = arrival_keyboard(event).inline_keyboard[0]
    assert all(len(button.callback_data.encode()) <= 64 for button in row)
    # The adult arrival carries the longest data: area buttons, shoo and the gift button.
    claimed = SimpleNamespace(
        id=2**63 - 1,
        pigeon_type="adult",
        type_state=json.dumps({"target": "wing", "gift_user_id": USER, "gift_status": "ready"}),
    )
    arrival = [b.callback_data for r in arrival_keyboard(claimed, True).inline_keyboard for b in r]
    flown = [b.callback_data for r in gift_keyboard(claimed, True).inline_keyboard for b in r]
    big = 2**63 - 1
    assert arrival == [
        f"pg:aim:{big}:cap",
        f"pg:aim:{big}:wing",
        f"pg:aim:{big}:tail",
        f"pg:shoo:{big}",
        f"pg:gift:{big}",
    ]
    assert flown == [f"pg:gift:{big}"]
    assert all(len(data.encode()) <= 64 for data in arrival + flown)
    assert all(
        len(data.encode()) <= 64
        for data in _timezone_buttons(
            SimpleNamespace(reply_markup=timezone_keyboard(CONTENT.timezones))
        )
    )


def _open_adult(store, clock):
    event_id = store.open_event(
        CHAT,
        clock.now(),
        clock.now() + timedelta(minutes=15),
        pigeon_name="Глаша",
        pigeon_type="adult",
        type_state=adult.initial_state(random.Random(3)),
    )
    return store.get_event(event_id)


async def test_adult_command_is_admin_only_and_scoped(harness, store) -> None:
    assert store.get_chat(CHAT).adult_enabled is False
    await harness.command("/adult on", user_id=USER)
    assert harness.session.said() == [texts.ADMIN_ONLY]
    assert store.get_chat(CHAT).adult_enabled is False
    await harness.command("/adult on")
    assert store.get_chat(CHAT).adult_enabled is True
    store.enable_chat(NEW_CHAT, NOON, "UTC")
    assert store.get_chat(NEW_CHAT).adult_enabled is False
    await harness.command("/adult off")
    assert store.get_chat(CHAT).adult_enabled is False


@pytest.mark.parametrize("args", ["", "maybe", "on now"])
async def test_adult_command_rejects_bad_arguments(harness, store, args) -> None:
    await harness.command(f"/adult {args}")
    assert harness.session.said() == [texts.ADULT_BAD]
    assert store.get_chat(CHAT).adult_enabled is False


async def test_adult_arrival_comes_from_deck_and_has_area_buttons(harness, store) -> None:
    store.set_adult_enabled(CHAT, True)
    store.save_pigeon_deck(CHAT, PigeonDeck(["adult"], "normal"))
    await harness.command("/arrive")
    event = store.current_open_event(CHAT)
    assert event.pigeon_type == "adult"
    call = harness.session.calls[-1]
    assert "Голубка" in call.text and adult.target(event) in adult.AREAS
    # Regression (manual smoke): the dove used to get a male name such as «Эдик».
    assert event.pigeon_name in CONTENT.dove_names
    assert event.pigeon_name not in CONTENT.names
    assert event.pigeon_name in call.text
    assert [b.callback_data for b in call.reply_markup.inline_keyboard[0]] == [
        f"pg:aim:{event.id}:cap",
        f"pg:aim:{event.id}:wing",
        f"pg:aim:{event.id}:tail",
    ]
    assert all(
        len(b.callback_data.encode()) <= 64
        for row in call.reply_markup.inline_keyboard
        for b in row
    )


async def test_only_winner_can_request_one_spoiler_photo(harness, store) -> None:
    store.set_adult_enabled(CHAT, True)
    event = _open_adult(store, harness.clock)
    await harness.tap(f"pg:gift:{event.id}")
    assert harness.session.toasts() == [texts.GIFT_NOT_WINNER]
    await harness.tap(f"pg:aim:{event.id}:{adult.target(event)}")
    assert store.balance(CHAT, USER) == 1
    edit = harness.session.edits()[-1]
    assert f"pg:gift:{event.id}" in [
        b.callback_data for row in edit.reply_markup.inline_keyboard for b in row
    ]
    await harness.tap(f"pg:gift:{event.id}", user_id=ADMIN)
    assert harness.gifts.calls == 0
    await harness.tap(f"pg:gift:{event.id}")
    assert harness.gifts.calls == 1
    photo = harness.session.photos()[0]
    assert photo.has_spoiler is True
    assert photo.caption == texts.GIFT_CAPTION
    await harness.tap(f"pg:gift:{event.id}")
    assert harness.gifts.calls == 1
    assert len(harness.session.photos()) == 1
    assert harness.session.toasts()[-1] == texts.GIFT_SENT


async def test_wrong_area_spends_attempt_without_gift(harness, store) -> None:
    store.set_adult_enabled(CHAT, True)
    event = _open_adult(store, harness.clock)
    wrong = next(area for area in adult.AREAS if area != adult.target(event))
    await harness.tap(f"pg:aim:{event.id}:{wrong}")
    assert store.balance(CHAT, USER) == 0
    assert adult.gift_owner(store.get_event(event.id)) is None
    await harness.tap(f"pg:aim:{event.id}:{adult.target(event)}")
    assert harness.session.toasts()[-1] == texts.HIT_ALREADY
    assert harness.gifts.calls == 0


async def test_gift_survives_closed_round_and_ignores_pause(harness, store) -> None:
    store.set_adult_enabled(CHAT, True)
    event = _open_adult(store, harness.clock)
    await harness.tap(f"pg:aim:{event.id}:{adult.target(event)}")
    store.close_event(event.id)
    store.set_paused_until(CHAT, NOON + timedelta(days=1))
    await harness.tap(f"pg:gift:{event.id}")
    assert len(harness.session.photos()) == 1
    assert "Голубка" in harness.session.edits()[-1].text


async def test_failed_gift_keeps_hit_and_can_be_retried(harness, store) -> None:
    store.set_adult_enabled(CHAT, True)
    event = _open_adult(store, harness.clock)
    await harness.tap(f"pg:aim:{event.id}:{adult.target(event)}")
    harness.gifts.fail = True
    await harness.tap(f"pg:gift:{event.id}")
    assert harness.session.photos() == []
    assert store.balance(CHAT, USER) == 1
    assert adult.gift_visible(store.get_event(event.id), True) is True
    harness.gifts.fail = False
    await harness.tap(f"pg:gift:{event.id}")
    assert len(harness.session.photos()) == 1


async def test_adult_off_and_other_chat_block_unclaimed_gift(harness, store) -> None:
    store.set_adult_enabled(CHAT, True)
    event = _open_adult(store, harness.clock)
    await harness.tap(f"pg:aim:{event.id}:{adult.target(event)}")
    await harness.tap(f"pg:gift:{event.id}", chat_id=NEW_CHAT)
    assert harness.gifts.calls == 0
    await harness.command("/adult off")
    await harness.tap(f"pg:gift:{event.id}")
    assert harness.gifts.calls == 0
    assert harness.session.toasts()[-1] == texts.GIFT_DISABLED


async def test_telegram_photo_failure_allows_retry(harness, store) -> None:
    store.set_adult_enabled(CHAT, True)
    event = _open_adult(store, harness.clock)
    await harness.tap(f"pg:aim:{event.id}:{adult.target(event)}")
    harness.session.fail_photos = True
    await harness.tap(f"pg:gift:{event.id}")
    assert len(harness.session.photos()) == 1
    assert adult.gift_visible(store.get_event(event.id), True) is True
    assert harness.session.toasts()[-1] == texts.GIFT_UNAVAILABLE
    harness.session.fail_photos = False
    await harness.tap(f"pg:gift:{event.id}")
    assert adult.gift_visible(store.get_event(event.id), True) is False


async def test_parallel_gift_taps_start_only_one_download(harness, store) -> None:
    store.set_adult_enabled(CHAT, True)
    event = _open_adult(store, harness.clock)
    await harness.tap(f"pg:aim:{event.id}:{adult.target(event)}")
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_download():
        harness.gifts.calls += 1
        started.set()
        await release.wait()
        return GiftImage(b"jpeg", "gift.jpg")

    harness.gifts.random_image = slow_download
    first = asyncio.create_task(harness.tap(f"pg:gift:{event.id}"))
    await asyncio.wait_for(started.wait(), timeout=1)
    await harness.tap(f"pg:gift:{event.id}")
    assert harness.session.toasts()[-1] == texts.GIFT_IN_PROGRESS
    release.set()
    await asyncio.wait_for(first, timeout=1)
    assert harness.gifts.calls == 1
    assert len(harness.session.photos()) == 1


async def test_scheduler_sender_keeps_unclaimed_gift_button(harness, store) -> None:
    from pigeon.telegram.messages import TelegramArrivalSender

    store.set_adult_enabled(CHAT, True)
    event = _open_adult(store, harness.clock)
    store.set_event_message(event.id, 500)
    await harness.tap(f"pg:aim:{event.id}:{adult.target(event)}")
    store.close_event(event.id)
    await TelegramArrivalSender(harness.bot, store, harness.clock).mark_flown(
        CHAT, 500, game.flown_text(store, store.get_event(event.id), harness.clock.now())
    )
    edit = harness.session.edits()[-1]
    assert [b.callback_data for row in edit.reply_markup.inline_keyboard for b in row] == [
        f"pg:gift:{event.id}"
    ]


def _answers(harness) -> list[AnswerCallbackQuery]:
    return [c for c in harness.session.calls if isinstance(c, AnswerCallbackQuery)]


async def test_successful_gift_answers_the_callback_once_without_text(harness, store) -> None:
    store.set_adult_enabled(CHAT, True)
    event = _open_adult(store, harness.clock)
    await harness.tap(f"pg:aim:{event.id}:{adult.target(event)}")
    before = len(_answers(harness))
    await harness.tap(f"pg:gift:{event.id}")
    answers = _answers(harness)[before:]
    assert len(answers) == 1
    assert answers[0].text is None
    assert len(harness.session.photos()) == 1


@pytest.mark.parametrize("failure", ["download", "telegram"])
async def test_failed_gift_answers_the_callback_once_with_an_alert(harness, store, failure) -> None:
    # Regression (D1): a loading toast and then a failure toast were two answers to one query.
    store.set_adult_enabled(CHAT, True)
    event = _open_adult(store, harness.clock)
    await harness.tap(f"pg:aim:{event.id}:{adult.target(event)}")
    harness.gifts.fail = failure == "download"
    harness.session.fail_photos = failure == "telegram"
    before = len(_answers(harness))
    await harness.tap(f"pg:gift:{event.id}")
    answers = _answers(harness)[before:]
    assert len(answers) == 1
    assert answers[0].text == texts.GIFT_UNAVAILABLE
    assert answers[0].show_alert is True
    assert adult.gift_visible(store.get_event(event.id), True) is True


async def test_rejected_late_answer_does_not_leave_the_gift_sending(harness, store) -> None:
    store.set_adult_enabled(CHAT, True)
    event = _open_adult(store, harness.clock)
    await harness.tap(f"pg:aim:{event.id}:{adult.target(event)}")
    original = harness.session.make_request

    async def too_old(bot, method, timeout=None):
        if isinstance(method, AnswerCallbackQuery):
            harness.session.calls.append(method)
            raise TelegramBadRequest(method=method, message="query is too old")
        return await original(bot, method, timeout)

    harness.session.make_request = too_old
    await harness.tap(f"pg:gift:{event.id}")
    assert len(harness.session.photos()) == 1
    assert adult.gift_visible(store.get_event(event.id), True) is False


async def test_gift_attempt_past_its_deadline_is_released_for_retry(
    harness, store, monkeypatch
) -> None:
    # Regression (D2): an attempt must end before its lease, then a retry is allowed.
    monkeypatch.setattr(adult, "GIFT_ATTEMPT_DEADLINE", timedelta(seconds=0.2))
    store.set_adult_enabled(CHAT, True)
    event = _open_adult(store, harness.clock)
    await harness.tap(f"pg:aim:{event.id}:{adult.target(event)}")

    async def hanging_download():
        harness.gifts.calls += 1
        await asyncio.sleep(10)

    harness.gifts.random_image = hanging_download
    before = len(_answers(harness))
    await asyncio.wait_for(harness.tap(f"pg:gift:{event.id}"), timeout=2)
    answers = _answers(harness)[before:]
    assert [a.text for a in answers] == [texts.GIFT_UNAVAILABLE]
    state = json.loads(store.get_event(event.id).type_state)
    assert state["gift_status"] == "ready"
    assert harness.session.photos() == []
    del harness.gifts.random_image
    await harness.tap(f"pg:gift:{event.id}")
    assert len(harness.session.photos()) == 1


MALFORMED = [
    "pg:aim:abc:cap",
    "pg:aim::cap",
    "pg:aim:1",
    "pg:aim:1:cap:x",
    "pg:aim:-1:cap",
    "pg:aim:99999999999999999999:cap",
    "pg:gift:abc",
    "pg:gift:",
    "pg:gift:1:2",
    "pg:gift:١",
    "pg:hit:abc",
    "pg:hit:",
    "pg:hit:1:2",
    "pg:shoo:abc",
    "pg:shoo:",
    "pg:shoo:1:2",
]


@pytest.mark.parametrize("data", MALFORMED)
async def test_malformed_callback_data_is_answered_once_without_changes(
    harness, store, data
) -> None:
    # Regression (N1): malformed ids raised ValueError and left the button spinning.
    store.set_adult_enabled(CHAT, True)
    event = _open_adult(store, harness.clock)
    state = store.get_event(event.id).type_state
    await harness.tap(data.replace(":1", f":{event.id}", 1) if ":1" in data else data)
    answers = _answers(harness)
    assert [a.text for a in answers] == [texts.HIT_NO_EVENT]
    assert store.hit_count(event.id) == 0
    assert store.get_event(event.id).type_state == state
    assert store.get_event(event.id).status == "open"
    assert store.balance(CHAT, USER) == 0
    assert harness.gifts.calls == 0


@pytest.mark.parametrize("area", ["", "CAP", "head", "wing2"])
async def test_unknown_area_is_answered_once_as_bad_area(harness, store, area) -> None:
    store.set_adult_enabled(CHAT, True)
    event = _open_adult(store, harness.clock)
    await harness.tap(f"pg:aim:{event.id}:{area}")
    assert [a.text for a in _answers(harness)] == [texts.HIT_BAD_AREA]
    assert store.hit_count(event.id) == 0


@pytest.mark.parametrize("closed", [False, True])
async def test_network_failure_on_late_answer_still_redraws_without_gift_button(
    harness, store, closed
) -> None:
    # Regression (round 2, N2): a network error on the answer used to abort the handler before
    # the redraw, leaving a stale gift button although the gift was already sent.
    store.set_adult_enabled(CHAT, True)
    event = _open_adult(store, harness.clock)
    await harness.tap(f"pg:aim:{event.id}:{adult.target(event)}")
    if closed:
        store.close_event(event.id)
    original = harness.session.make_request

    async def network_down(bot, method, timeout=None):
        if isinstance(method, AnswerCallbackQuery):
            harness.session.calls.append(method)
            raise TelegramNetworkError(method=method, message="connection reset")
        return await original(bot, method, timeout)

    harness.session.make_request = network_down
    edits_before = len(harness.session.edits())
    await harness.tap(f"pg:gift:{event.id}")
    assert len(harness.session.photos()) == 1
    state = json.loads(store.get_event(event.id).type_state)
    assert state["gift_status"] == "sent"
    edits = harness.session.edits()[edits_before:]
    assert len(edits) == 1
    buttons = [
        b.callback_data
        for row in (edits[0].reply_markup.inline_keyboard if edits[0].reply_markup else [])
        for b in row
    ]
    assert f"pg:gift:{event.id}" not in buttons
    assert ("Голубка" in edits[0].text) is True


# --- Separate commands (ADR-0013, issue #13) -------------------------------------------------

COMMON = [name for name, _ in texts.COMMON_COMMANDS]
ADMIN_ONLY_COMMANDS = [name for name, _ in texts.ADMIN_COMMANDS]
ALL_COMMANDS = COMMON + ADMIN_ONLY_COMMANDS
ANON_ADMIN = {"user_id": 1087968824, "sender_chat": Chat(id=CHAT, type="supergroup")}


def _dump(store) -> str:
    """Game state only: ADR-0018 intentionally counts even rejected commands."""
    return "\n".join(
        line
        for line in store._conn.iterdump()
        if '"usage_' not in line and not line.startswith("CREATE TABLE usage_")
    )


def _no_member_lookups(harness) -> bool:
    return not any(isinstance(c, GetChatMember) for c in harness.session.calls)


def test_menu_lists_exactly_the_issue_commands() -> None:
    assert COMMON == ["pigeon", "help", "stats", "profile", "fortune"]
    assert ADMIN_ONLY_COMMANDS == ["setup", "quiet", "pause", "arrive", "adult"]
    for name, description in texts.COMMON_COMMANDS + texts.ADMIN_COMMANDS:
        assert re.fullmatch(r"[a-z0-9_]{1,32}", name)  # Bot API rule for BotCommand.command
        assert 1 <= len(description) <= 256


@pytest.mark.parametrize(
    "text",
    [
        "/pigeon",
        "/help",
        "/stats",
        "/stats week",
        "/stats all",
        "/profile",
        "/setup",
        "/setup Asia/Tokyo",
        "/quiet 23 9",
        "/quiet 9 23",
        "/pause off",
        "/pause 3w",
        "/adult off",
        "/adult maybe",
    ],
)
async def test_command_with_the_bot_suffix_answers_like_the_bare_one(store, clock, text) -> None:
    replies = []
    for suffix in ("", f"@{BOT_USERNAME}"):
        h = Harness(store, clock)
        name, _, rest = text.partition(" ")
        await h.command(f"{name}{suffix} {rest}".strip())
        replies.append(h.session.said())
        await h.bot.session.close()
    assert replies[0] == replies[1] and len(replies[0]) == 1


async def test_fortune_and_arrive_work_with_the_bot_suffix(harness, store) -> None:
    await harness.command(f"/fortune@{BOT_USERNAME}", user_id=USER)
    assert harness.session.said()[-1] != texts.FORTUNE_ALREADY
    await harness.command(f"/fortune@{BOT_USERNAME}", user_id=USER)
    assert harness.session.said()[-1] == texts.FORTUNE_ALREADY
    await harness.command(f"/arrive@{BOT_USERNAME}")
    assert store.current_open_event(CHAT) is not None


@pytest.mark.parametrize("name", ALL_COMMANDS)
async def test_command_for_another_bot_is_ignored(harness, store, name) -> None:
    before = _dump(store)
    await harness.command(f"/{name}@other_bot on")
    assert harness.session.said() == []
    assert _dump(store) == before


@pytest.mark.parametrize("name", ALL_COMMANDS)
async def test_every_command_answers_only_in_groups(harness, store, name) -> None:
    before = _dump(store)
    await harness.command(f"/{name}", chat_id=ADMIN, chat_type="private")
    assert harness.session.said() == [texts.NOT_GROUP]
    assert _dump(store) == before


@pytest.mark.parametrize("name", ALL_COMMANDS)
async def test_every_menu_command_has_a_handler(harness, name) -> None:
    await harness.command(f"/{name}")
    assert harness.session.said(), f"/{name} got no answer"


async def test_help_and_setup_work_before_setup(harness, store) -> None:
    await harness.command("/help", user_id=USER, chat_id=NEW_CHAT)
    await harness.command(f"/help@{BOT_USERNAME}", user_id=USER, chat_id=NEW_CHAT)
    assert harness.session.said() == [texts.HELP, texts.HELP]
    await harness.command("/setup Europe/Moscow", chat_id=NEW_CHAT)
    assert harness.session.said()[-1] == texts.SETUP_DONE.format(tz="Europe/Moscow")
    assert store.is_enabled(NEW_CHAT)


@pytest.mark.parametrize(
    "text",
    ["/pigeon", "/stats", "/profile", "/fortune", "/quiet 23 9", "/pause 24h", "/arrive"]
    + ["/adult on"],
)
async def test_other_commands_wait_for_setup_even_for_admins(harness, store, text) -> None:
    before = _dump(store)
    await harness.command(text, chat_id=NEW_CHAT)
    assert harness.session.said() == [texts.NOT_ENABLED]
    assert "/setup" in texts.NOT_ENABLED
    assert _dump(store) == before


@pytest.mark.parametrize(
    "text", ["/setup", "/setup Asia/Tokyo", "/quiet 23 9", "/pause 24h", "/arrive", "/adult on"]
)
async def test_admin_commands_refuse_members_and_change_nothing(harness, store, text) -> None:
    before = _dump(store)
    await harness.command(text, user_id=USER)
    assert harness.session.said() == [texts.ADMIN_ONLY]
    assert _dump(store) == before


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/quiet 22 8", texts.QUIET_SET.format(start=22, end=8)),
        ("/pause off", texts.PAUSE_OFF),
        ("/adult on", texts.ADULT_ON),
        ("/setup Asia/Omsk", texts.SETUP_DONE.format(tz="Asia/Omsk")),
    ],
)
async def test_anonymous_admin_runs_admin_commands(harness, text, expected) -> None:
    await harness.command(text, **ANON_ADMIN)
    assert harness.session.said() == [expected]
    assert _no_member_lookups(harness)


async def test_anonymous_admin_can_release_a_pigeon(harness, store) -> None:
    await harness.command("/arrive", **ANON_ADMIN)
    assert store.current_open_event(CHAT) is not None
    assert _no_member_lookups(harness)


async def test_quiet_hours_are_set_and_bad_values_change_nothing(harness, store) -> None:
    await harness.command("/quiet 22 8")
    chat = store.get_chat(CHAT)
    assert (chat.quiet_start, chat.quiet_end) == (22, 8)
    for bad in ("/quiet 9 23", "/quiet 23", "/quiet a b", "/quiet 24 9"):
        await harness.command(bad)
    assert harness.session.said()[1:] == [texts.QUIET_BAD] * 4
    assert "/quiet 23 9" in texts.QUIET_BAD
    chat = store.get_chat(CHAT)
    assert (chat.quiet_start, chat.quiet_end) == (22, 8)


async def test_arrive_twice_does_not_open_a_second_pigeon(harness, store) -> None:
    await harness.command("/arrive")
    first = store.current_open_event(CHAT)
    await harness.command("/arrive")
    assert harness.session.said()[-1] == texts.ALREADY_SITTING
    assert store.current_open_event(CHAT).id == first.id


async def test_stats_week_equals_default_and_garbage_falls_back(harness, store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, event.id, ADMIN, "Anna")
    for text in ("/stats", "/stats week", "/stats garbage", "/stats all"):
        await harness.command(text)
    week, week_again, garbage, overall = harness.session.said()
    assert week == week_again == garbage
    assert overall != week and "Золотой плевок" in overall


PIGEON_ARGS = [
    "stats",
    "stats all",
    "help",
    "profile",
    "fortune",
    "setup",
    "setup Europe/London",
    "quiet 22 8",
    "pause 24h",
    "arrive",
    "adult on",
    "foo bar",
]


@pytest.mark.parametrize("args", PIGEON_ARGS)
@pytest.mark.parametrize("sitting", [False, True])
async def test_pigeon_with_any_arguments_only_shows_the_status(
    harness, store, clock, args, sitting
) -> None:
    if sitting:
        game.arrive(store, clock, CHAT, VISITOR)
    before = _dump(store)
    await harness.command("/pigeon")
    await harness.command(f"/pigeon {args}")
    await harness.command(f"/pigeon@{BOT_USERNAME} {args}")
    bare, with_args, with_suffix = harness.session.said()
    assert with_args == with_suffix == bare
    assert bare == (texts.status_sitting("Гена", 0) if sitting else texts.STATUS_AWAY)
    assert _dump(store) == before  # no subcommand ran: no setup, pause, fortune or arrival
    assert harness.session.calls[-1].reply_markup is None  # no setup keyboard either


@pytest.mark.parametrize("args", ["setup Europe/Moscow", "help", "setup"])
async def test_pigeon_with_arguments_in_a_new_chat_does_not_set_it_up(harness, store, args):
    await harness.command(f"/pigeon {args}", chat_id=NEW_CHAT)
    assert harness.session.said() == [texts.NOT_ENABLED]
    assert store.get_chat(NEW_CHAT) is None


async def test_pigeon_with_arguments_in_private_is_not_a_game(harness) -> None:
    await harness.command("/pigeon setup Europe/Moscow", chat_id=ADMIN, chat_type="private")
    assert harness.session.said() == [texts.NOT_GROUP]


def test_texts_name_only_the_new_commands() -> None:
    public = {
        name: value
        for name, value in vars(texts).items()
        if name.isupper() and isinstance(value, str)
    }
    old = {name for name, value in public.items() if re.search(r"/pigeon [a-z]", value)}
    assert old == set()
    for name in ("NOT_ENABLED", "TZ_BAD", "TZ_UNKNOWN"):
        assert "/setup" in public[name]
    for name in ("SETUP_DONE", "STATUS_AWAY"):
        assert "/arrive" in public[name]
    assert "/pause 24h" in texts.PAUSE_BAD and "/adult on" in texts.ADULT_BAD
    # [с] [до] stay plain text: angle-bracket placeholders would break parse_mode=HTML.
    assert set(re.findall(r"</?([a-zA-Zа-яА-Я]+)", texts.HELP)) == {"b"}


@pytest.mark.parametrize("text", ["/quiet 23 9", "/pause 24h", "/arrive", "/adult on"])
async def test_member_in_a_new_chat_hears_about_setup_before_rights(harness, store, text) -> None:
    # Guard order: group, then setup, then admin rights — as the old /pigeon dispatcher did.
    await harness.command(text, user_id=USER, chat_id=NEW_CHAT)
    assert harness.session.said() == [texts.NOT_ENABLED]
    assert _no_member_lookups(harness)
    assert store.get_chat(NEW_CHAT) is None


@pytest.mark.parametrize("arg", ["all", "ALL", "All"])
async def test_stats_all_ignores_case(harness, store, clock, arg) -> None:
    event = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, event.id, ADMIN, "Anna")
    await harness.command("/stats all")
    await harness.command(f"/stats {arg}")
    reference, answer = harness.session.said()
    assert answer == reference and "Золотой плевок" in answer


@pytest.mark.parametrize("planned", [False, True])
@pytest.mark.parametrize("delivered", [False, True])
async def test_manual_send_in_flight_does_not_spend_daily_plan(
    harness, store, clock, monkeypatch, planned, delivered
):
    from pigeon.scheduler import arrivals
    from pigeon.telegram.messages import TelegramArrivalSender
    from rigged import EveryDay

    class Early(EveryDay):
        def randrange(self, *args):
            return 0

    clock.set(datetime(2026, 11, 2, 5, 0, tzinfo=UTC))  # Moscow08:00
    rng = Early(29)
    sender = TelegramArrivalSender(harness.bot, store, clock, CONTENT.quests)
    if planned:
        await arrivals.tick(store, clock, rng, sender, CONTENT)
        assert store.get_plan(CHAT, "2026-11-02").status == "pending"
    clock.set(datetime(2026, 11, 2, 5, 59, tzinfo=UTC))  # just before due09:00
    entered, release = asyncio.Event(), asyncio.Event()
    original = harness.session.make_request
    first_send = True

    async def delayed_manual(bot, method, timeout=None):
        nonlocal first_send
        if isinstance(method, SendMessage) and first_send:
            first_send = False
            entered.set()
            await release.wait()
            if not delivered:
                raise RuntimeError("controlled failed manual send")
        return await original(bot, method, timeout)

    monkeypatch.setattr(harness.session, "make_request", delayed_manual)
    task = asyncio.create_task(harness.command("/arrive"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        manual = store.current_open_event(CHAT)
        assert manual is not None and manual.message_id is None
        # Tick while network call is outstanding, first before due time and then when due.
        await arrivals.tick(store, clock, rng, sender, CONTENT)
        clock.set(datetime(2026, 11, 2, 6, 0, tzinfo=UTC))
        await arrivals.tick(store, clock, rng, sender, CONTENT)
        assert store.get_plan(CHAT, "2026-11-02").status == "pending"
        release.set()
        if delivered:
            await task
            assert store.get_event(manual.id).message_id is not None
            clock.set(manual.closes_at)
        else:
            with pytest.raises(RuntimeError, match="controlled failed manual send"):
                await task
            assert store.get_event(manual.id) is None
        await arrivals.tick(store, clock, rng, sender, CONTENT)
        assert store.get_plan(CHAT, "2026-11-02").status == ("skipped" if delivered else "fired")
        auto_count = store.count_auto_events_since(CHAT, datetime(2026, 11, 2, tzinfo=UTC))
        assert auto_count == (0 if delivered else 1)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
