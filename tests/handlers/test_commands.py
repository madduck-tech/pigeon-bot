"""Real aiogram routing: updates go through the Dispatcher, API calls stop at a fake session."""

import random
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import ClientDecodeError, TelegramBadRequest, TelegramNetworkError
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    GetChatMember,
    SendDice,
    SendMessage,
    SendPoll,
)
from aiogram.types import (
    CallbackQuery,
    Chat,
    Dice,
    Message,
    PhotoSize,
    Update,
    User,
)

from pigeon.clock import FixedClock
from pigeon.content import load_content
from pigeon.handlers.pigeon import build_router
from pigeon.render import texts
from pigeon.repo.store import Store
from pigeon.services import game

CHAT = -1001
NEW_CHAT = -100777
ADMIN = 1
USER = 2
NOON = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
CONTENT = load_content(Path(__file__).resolve().parents[2] / "content")


class FakeSession(BaseSession):
    def __init__(self, dice_value: int = 6) -> None:
        super().__init__()
        self.calls = []
        self.dice_value = dice_value
        self.dice_queue: list[int] = []

    async def close(self) -> None:
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        chat = Chat(id=CHAT, type="supergroup")
        when = NOON
        if isinstance(method, GetChatMember):
            return SimpleNamespace(status="administrator" if method.user_id == ADMIN else "member")
        if isinstance(method, SendDice):
            return Message(
                message_id=900,
                date=when,
                chat=chat,
                dice=Dice(
                    emoji="🎯",
                    value=self.dice_queue.pop(0) if self.dice_queue else self.dice_value,
                ),
            ).as_(bot)
        if isinstance(method, SendMessage):
            return Message(message_id=901, date=when, chat=chat, text=method.text)
        if isinstance(method, SendPoll):
            return Message(message_id=902, date=when, chat=chat)
        if isinstance(method, AnswerCallbackQuery):
            return True
        return True

    async def stream_content(self, *args, **kwargs) -> AsyncGenerator[bytes, None]:
        yield b""

    def said(self) -> list[str]:
        return [c.text for c in self.calls if isinstance(c, SendMessage)]

    def toasts(self) -> list[str]:
        return [c.text for c in self.calls if isinstance(c, AnswerCallbackQuery)]

    def edits(self) -> list[str]:
        return [c.text for c in self.calls if isinstance(c, EditMessageText)]

    def polls(self) -> list[SendPoll]:
        return [c for c in self.calls if isinstance(c, SendPoll)]

    def throws(self) -> int:
        return sum(isinstance(c, SendDice) for c in self.calls)


class Harness:
    def __init__(self, store: Store, clock: FixedClock) -> None:
        self.store = store
        self.clock = clock
        self.session = FakeSession()
        self.bot = Bot("42:TEST", session=self.session)
        self.dp = Dispatcher()

        async def no_sleep(seconds: float) -> None:
            pass

        router = build_router(store, clock, CONTENT, random.Random(1), sleep=no_sleep)
        self.dp.include_router(router)
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
        reply_to: Message | None = None,
        first_name: str = "Anna",
    ) -> None:
        message = Message(
            message_id=self.update_id + 1,
            date=NOON,
            chat=Chat(id=CHAT, type="supergroup"),
            from_user=User(id=user_id, is_bot=False, first_name=first_name),
            text=text,
            entities=[
                {"type": "bot_command", "offset": 0, "length": len(text.split()[0])},
            ],
            reply_to_message=reply_to,
        )
        await self._feed(message=message)

    async def tap(self, data: str, user_id: int = USER) -> None:
        query = CallbackQuery(
            id="1",
            from_user=User(id=user_id, is_bot=False, first_name="Boris"),
            chat_instance="c",
            data=data,
            message=Message(
                message_id=500,
                date=NOON,
                chat=Chat(id=CHAT, type="supergroup"),
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
    assert first.text.startswith("🔮 ") and first.text.removeprefix("🔮 ") in CONTENT.fortunes
    assert first.reply_parameters is not None or first.reply_to_message_id is not None
    await harness.command("/pigeon fortune", user_id=USER)
    assert harness.session.said()[-1] == texts.FORTUNE_ALREADY


async def test_profile_shows_points_achievements_and_trophy(harness, store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    game.hit(store, clock, CHAT, event.id, ADMIN, "Anna")
    store.award(CHAT, ADMIN, "bullseye", clock.now())
    clock.set(clock.now() + timedelta(days=2))
    await harness.command("/pigeon profile")
    text = harness.session.said()[-1]
    assert "Очки: 1" in text and "В яблочко" in text and "2 дн." in text


async def test_profile_of_a_newcomer(harness) -> None:
    await harness.command("/pigeon profile", user_id=USER)
    text = harness.session.said()[-1]
    assert "Очки: 0" in text and "пока нет" in text


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


async def test_dart_six_announces_bullseye_in_the_reply(harness, store, clock) -> None:
    event = game.arrive(store, clock, CHAT)
    await harness.tap(f"pg:dart:{event.id}")
    assert "В яблочко" in harness.session.said()[-1]
    assert store.achievements_of(CHAT, USER) == ["bullseye"]


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


async def test_plain_messages_are_still_counted(harness, store) -> None:
    await harness.service(CHAT, text="привет")
    assert store.messages_since(CHAT, "2026-11-02T00:00:00+00:00") == 1


@pytest.mark.parametrize("name", ["Europe", "America", "Etc", "no/such"])
async def test_setup_rejects_a_timezone_folder_without_crashing(harness, name) -> None:
    # Regression: on Python 3.12 "Europe" raised IsADirectoryError and the bot said nothing.
    await harness.command(f"/pigeon setup {name}")
    assert harness.session.said() == [texts.TZ_BAD]


async def test_repeating_setup_without_a_timezone_keeps_the_current_one(harness, store) -> None:
    await harness.command("/pigeon setup")
    assert store.get_chat(CHAT).timezone == "Europe/Moscow"


def _said_by(user_id: int, first_name: str, is_bot: bool = False, **fields) -> Message:
    return Message(
        message_id=400,
        date=NOON,
        chat=Chat(id=CHAT, type="supergroup"),
        from_user=User(id=user_id, is_bot=is_bot, first_name=first_name),
        **fields,
    )


def _photo() -> list[PhotoSize]:
    return [PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)]


def _duel_button(harness) -> str:
    markup = next(c for c in harness.session.calls if getattr(c, "reply_markup", None)).reply_markup
    return markup.inline_keyboard[0][0].callback_data


async def _start_duel(harness) -> str:
    await harness.command("/pigeon duel", reply_to=_said_by(USER, "Boris"))
    return _duel_button(harness)


async def test_duel_by_reply_posts_a_challenge_without_pinging(harness) -> None:
    await _start_duel(harness)
    call = harness.session.calls[-1]
    assert call.text == texts.DUEL_CHALLENGE.format(a="Anna", b="Boris")
    assert call.entities is None and call.reply_parameters is None


async def test_duel_by_username(harness, store) -> None:
    store.remember_member(CHAT, USER, "Boris", "boris_b")
    await harness.command("/pigeon duel @Boris_B")
    assert harness.session.said() == [texts.DUEL_CHALLENGE.format(a="Anna", b="Boris")]


async def test_duel_with_someone_who_only_chatted(harness) -> None:
    user = User(id=USER, is_bot=False, first_name="Boris", username="boris_b")
    await harness.service(CHAT, text="привет", from_user=user)
    await harness.command("/pigeon duel @boris_b")
    assert harness.session.said() == [texts.DUEL_CHALLENGE.format(a="Anna", b="Boris")]


async def test_duel_with_an_unknown_username(harness, store) -> None:
    await harness.command("/pigeon duel @nobody")
    assert harness.session.said() == [texts.DUEL_UNKNOWN]


async def test_duel_without_a_target(harness) -> None:
    await harness.command("/pigeon duel")
    assert harness.session.said() == [texts.DUEL_USAGE]


async def test_duel_with_yourself(harness) -> None:
    await harness.command("/pigeon duel", reply_to=_said_by(ADMIN, "Anna"))
    assert harness.session.said() == [texts.DUEL_SELF]


async def test_duel_with_a_bot(harness) -> None:
    await harness.command("/pigeon duel", reply_to=_said_by(99, "Bot", is_bot=True))
    assert harness.session.said() == [texts.DUEL_BOT]


async def test_second_duel_is_refused_while_one_is_live(harness) -> None:
    await _start_duel(harness)
    await harness.command("/pigeon duel", reply_to=_said_by(USER, "Boris"))
    assert harness.session.said()[-1] == texts.DUEL_BUSY


async def test_failed_challenge_message_does_not_hold_the_chat(harness, store, clock) -> None:
    real = harness.session.make_request

    async def fails(bot, method, timeout=None):
        if isinstance(method, SendMessage):
            raise RuntimeError("telegram unavailable")
        return await real(bot, method, timeout)

    harness.session.make_request = fails
    with pytest.raises(RuntimeError):
        await harness.command("/pigeon duel", reply_to=_said_by(USER, "Boris"))
    harness.session.make_request = real
    await harness.command("/pigeon duel", reply_to=_said_by(USER, "Boris"))
    assert harness.session.said()[-1] == texts.DUEL_CHALLENGE.format(a="Anna", b="Boris")


async def test_only_the_opponent_can_press_accept(harness) -> None:
    button = await _start_duel(harness)
    await harness.tap(button, user_id=ADMIN)
    assert harness.session.toasts() == [texts.DUEL_NOT_YOURS]
    assert harness.session.throws() == 0


async def test_full_bout_from_accept_to_result(harness, store) -> None:
    harness.session.dice_queue = [6, 1, 3, 3, 1, 6, 5, 2]
    button = await _start_duel(harness)
    await harness.tap(button, user_id=USER)
    assert harness.session.throws() == 8
    assert harness.session.edits() == [texts.DUEL_FIGHT.format(a="Anna", b="Boris")]
    result = harness.session.calls[-1].text
    assert "Раунд 2: 3 : 3, ничья" in result
    assert "Счёт 2 : 1" in result
    assert texts.DUEL_WIN.format(name="Anna", points=3) in result
    assert "Дуэлянт" in result
    assert store.balance(CHAT, ADMIN) == 3
    assert store.balance(CHAT, USER) == 0


async def test_double_tap_on_accept_runs_one_bout(harness) -> None:
    harness.session.dice_queue = [6, 1] * 3
    button = await _start_duel(harness)
    await harness.tap(button, user_id=USER)
    await harness.tap(button, user_id=USER)
    assert harness.session.throws() == 6
    assert harness.session.toasts()[-1] == texts.DUEL_GONE


async def test_constant_ties_end_in_a_draw(harness, store) -> None:
    button = await _start_duel(harness)
    await harness.tap(button, user_id=USER)
    assert texts.DUEL_DRAW in harness.session.calls[-1].text
    assert store.balance(CHAT, ADMIN) == store.balance(CHAT, USER) == 0


async def test_error_during_a_bout_leaves_it_for_the_sweep(harness, store, clock) -> None:
    from pigeon.services import duels

    button = await _start_duel(harness)
    real = harness.session.make_request

    async def fails(bot, method, timeout=None):
        if isinstance(method, SendDice):
            raise RuntimeError("telegram unavailable")
        return await real(bot, method, timeout)

    harness.session.make_request = fails
    with pytest.raises(RuntimeError):
        await harness.tap(button, user_id=USER)
    assert store.balance(CHAT, ADMIN) == store.balance(CHAT, USER) == 0
    clock.set(clock.now() + duels.BOUT_TIME)
    assert len(duels.sweep(store, clock)) == 1


async def test_decline_edits_the_challenge(harness, store) -> None:
    await _start_duel(harness)
    button = _duel_button(harness).replace("duel_ok", "duel_no")
    await harness.tap(button, user_id=USER)
    assert harness.session.edits() == [texts.DUEL_DECLINED.format(b="Boris")]
    assert harness.session.throws() == 0


async def test_duel_works_during_quiet_hours_and_pause(harness, store, clock) -> None:
    store.set_paused_until(CHAT, clock.now() + timedelta(days=1))
    clock.set(clock.now() + timedelta(hours=13))  # 01:00 Moscow
    button = await _start_duel(harness)
    await harness.tap(button, user_id=USER)
    assert harness.session.throws() > 0


async def test_duel_callback_data_fits_telegram(harness) -> None:
    from pigeon.telegram.messages import duel_keyboard

    row = duel_keyboard(2**63 - 1).inline_keyboard[0]
    assert all(len(button.callback_data.encode()) <= 64 for button in row)


async def test_photo_command_replies_silently_with_the_poll(harness) -> None:
    await harness.command(
        "/pigeon photo", user_id=USER, reply_to=_said_by(ADMIN, "A", photo=_photo())
    )
    poll = harness.session.polls()[0]
    assert poll.question == texts.PHOTO_QUESTION
    assert [option.text for option in poll.options] == list(texts.PHOTO_OPTIONS)
    assert poll.reply_parameters.message_id == 400
    assert poll.disable_notification is True


async def test_photo_command_needs_a_photo(harness) -> None:
    await harness.command("/pigeon photo", reply_to=_said_by(USER, "Boris", text="x"))
    await harness.command("/pigeon photo")
    assert harness.session.said() == [texts.PHOTO_USAGE] * 2
    assert harness.session.polls() == []


async def test_photo_command_works_at_night_but_only_once_an_hour(harness, clock) -> None:
    clock.set(clock.now() + timedelta(hours=13))  # 01:00 Moscow
    target = _said_by(USER, "Boris", photo=_photo())
    await harness.command("/pigeon photo", reply_to=target)
    await harness.command("/pigeon photo", reply_to=target)
    assert len(harness.session.polls()) == 1
    assert harness.session.said() == [texts.PHOTO_COOLDOWN]


async def test_photo_caption_starts_a_poll_and_is_still_counted(harness, store) -> None:
    await harness.service(CHAT, photo=_photo(), caption="смотрите, голубь")
    assert len(harness.session.polls()) == 1
    assert store.messages_since(CHAT, "2026-11-02T00:00:00+00:00") == 1


async def test_photo_with_another_caption_is_ignored(harness) -> None:
    await harness.service(CHAT, photo=_photo(), caption="котик")
    await harness.service(CHAT, text="голубь")
    assert harness.session.polls() == []


async def test_caption_in_a_chat_without_setup_is_ignored(harness) -> None:
    await harness.service(-555, photo=_photo(), caption="голубь")
    assert harness.session.polls() == []


async def test_captions_are_not_stored(harness, store, tmp_path) -> None:
    marker = "голубь-секретнаяподпись"
    await harness.service(CHAT, photo=_photo(), caption=f"голубь {marker}")
    dump = "\n".join(store._conn.iterdump())
    assert marker not in dump


async def test_name_vote_is_for_admins_only(harness) -> None:
    await harness.command("/pigeon name", user_id=USER)
    assert harness.session.said() == [texts.ADMIN_ONLY]
    assert harness.session.polls() == []


async def test_name_vote_opens_once(harness, store) -> None:
    await harness.command("/pigeon name")
    await harness.command("/pigeon name")
    poll = harness.session.polls()[0]
    assert poll.question == texts.NAME_QUESTION
    names = {option.text for option in poll.options}
    assert len(names) == 5 and names <= set(CONTENT.names)
    assert harness.session.said() == [texts.NAME_ALREADY]
    assert store.open_name_vote(CHAT).message_id == 902


async def test_help_mentions_what_the_bot_sees_and_the_new_commands(harness) -> None:
    await harness.command("/pigeon help")
    text = harness.session.said()[0]
    assert all(word in text for word in ("/pigeon duel", "/pigeon photo", "/pigeon name"))
    assert "username" in text and "не сохраняет" in text


async def test_accepted_duel_runs_even_if_the_tap_cannot_be_confirmed(harness, store) -> None:
    # Regression: "query is too old" after a restart left the duel active with no bout.
    harness.session.dice_queue = [6, 1] * 3
    button = await _start_duel(harness)
    real = harness.session.make_request

    async def too_old(bot, method, timeout=None):
        if isinstance(method, (AnswerCallbackQuery, EditMessageText)):
            raise TelegramBadRequest(method, "query is too old")
        return await real(bot, method, timeout)

    harness.session.make_request = too_old
    await harness.tap(button, user_id=USER)
    assert harness.session.throws() == 6
    assert store.balance(CHAT, ADMIN) == 3


async def test_decline_still_edits_if_the_tap_cannot_be_confirmed(harness) -> None:
    await _start_duel(harness)
    button = _duel_button(harness).replace("duel_ok", "duel_no")
    real = harness.session.make_request

    async def too_old(bot, method, timeout=None):
        if isinstance(method, AnswerCallbackQuery):
            raise TelegramBadRequest(method, "query is too old")
        return await real(bot, method, timeout)

    harness.session.make_request = too_old
    await harness.tap(button, user_id=USER)
    harness.session.make_request = real
    assert harness.session.edits() == [texts.DUEL_DECLINED.format(b="Boris")]


async def test_bout_stops_throwing_once_it_was_swept(harness, store, clock) -> None:
    from pigeon.services import duels

    button = await _start_duel(harness)
    real = harness.session.make_request

    async def stalls(bot, method, timeout=None):
        if isinstance(method, SendDice) and harness.session.throws() == 1:
            clock.set(clock.now() + duels.BOUT_TIME)
            duels.sweep(store, clock)
        return await real(bot, method, timeout)

    harness.session.make_request = stalls
    await harness.tap(button, user_id=USER)
    assert harness.session.throws() == 2  # the round in progress, then it stops
    assert store.balance(CHAT, ADMIN) == store.balance(CHAT, USER) == 0


async def test_failed_connection_does_not_lock_out_the_name_vote(harness, store) -> None:
    # Regression: a refused connection cost the month and answered "already running" for a day.
    real = harness.session.make_request

    async def refused(bot, method, timeout=None):
        if isinstance(method, SendPoll):
            raise TelegramNetworkError(method, "connection refused")
        return await real(bot, method, timeout)

    harness.session.make_request = refused
    with pytest.raises(TelegramNetworkError):
        await harness.command("/pigeon name")
    assert store.open_name_vote(CHAT) is None
    harness.session.make_request = real
    await harness.command("/pigeon name")
    assert len(harness.session.polls()) == 1


async def test_non_json_gateway_error_does_not_stall_an_accepted_duel(harness, store) -> None:
    # Regression: an HTML 502 is not a TelegramAPIError, and it stopped the bout.
    harness.session.dice_queue = [6, 1] * 3
    button = await _start_duel(harness)
    real = harness.session.make_request

    async def gateway(bot, method, timeout=None):
        if isinstance(method, (AnswerCallbackQuery, EditMessageText)):
            raise ClientDecodeError("bad gateway", ValueError("not json"), "<html>")
        return await real(bot, method, timeout)

    harness.session.make_request = gateway
    await harness.tap(button, user_id=USER)
    assert harness.session.throws() == 6
    assert store.balance(CHAT, ADMIN) == 3
