import random
from datetime import UTC, datetime, timedelta

import pytest
from aiogram.exceptions import TelegramMigrateToChat
from aiogram.methods import SendMessage

from pigeon import app
from pigeon.clock import FixedClock
from pigeon.content import Fortune, FortuneFormat
from pigeon.render import texts
from pigeon.repo.store import Store
from pigeon.scheduler import fortunes
from pigeon.services import fortune
from rigged import CONTENT

CHAT = -7101
OTHER = -7102
TZ = "Europe/Moscow"  # UTC+3, no DST
LINES = [Fortune("test", FortuneFormat.CLASSIC, "а < б")]
NINE = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)  # 09:00 Moscow, quiet hours just ended
TEN = NINE + timedelta(hours=1)
NIGHT = datetime(2026, 11, 2, 21, 0, tzinfo=UTC)  # 00:00 Moscow on the next day


class Sender:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.fail_for: set[int] = set()

    async def send_text(self, chat_id: int, text: str) -> None:
        if chat_id in self.fail_for:
            raise RuntimeError("telegram unavailable")
        self.sent.append((chat_id, text))


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "pigeon.db")
    s.enable_chat(CHAT, NINE, TZ)
    yield s
    s.close()


async def _tick(store, clock, sender) -> None:
    await fortunes.tick(store, clock, random.Random(1), sender, LINES, quests=CONTENT.quests)


async def test_comes_an_hour_after_quiet_hours(store) -> None:
    sender = Sender()
    clock = FixedClock(TEN - timedelta(minutes=1))
    await _tick(store, clock, sender)
    assert sender.sent == []
    clock.set(TEN)
    await _tick(store, clock, sender)
    assert len(sender.sent) == 1
    assert "Новая неделя" in sender.sent[0][1]


async def test_once_per_local_day(store) -> None:
    sender = Sender()
    clock = FixedClock(TEN)
    await _tick(store, clock, sender)
    clock.set(TEN + timedelta(hours=5))
    await _tick(store, clock, sender)
    assert len(sender.sent) == 1
    clock.set(TEN + timedelta(days=1))
    await _tick(store, clock, sender)
    assert len(sender.sent) == 1  # Tuesday has no daily post


async def test_follows_the_chats_own_quiet_hours(store) -> None:
    store.set_quiet_hours(CHAT, 22, 12)
    sender = Sender()
    clock = FixedClock(TEN)
    await _tick(store, clock, sender)
    assert sender.sent == []
    clock.set(TEN + timedelta(hours=3))  # 13:00 Moscow
    await _tick(store, clock, sender)
    assert len(sender.sent) == 1


async def test_narrow_daytime_window_still_gets_a_post(store) -> None:
    # With one free hour, "an hour after the end" would fall into quiet hours every day.
    store.set_quiet_hours(CHAT, 10, 9)
    sender = Sender()
    await _tick(store, FixedClock(NINE), sender)
    assert len(sender.sent) == 1


async def test_silent_at_night_and_after_a_late_restart(store) -> None:
    sender = Sender()
    await _tick(store, FixedClock(NIGHT), sender)
    assert sender.sent == []


async def test_silent_during_a_pause_and_the_day_is_not_used_up(store) -> None:
    store.set_paused_until(CHAT, TEN + timedelta(hours=2))
    sender = Sender()
    clock = FixedClock(TEN)
    await _tick(store, clock, sender)
    assert sender.sent == []
    clock.set(TEN + timedelta(hours=2))
    await _tick(store, clock, sender)
    assert len(sender.sent) == 1


async def test_failed_send_is_tried_again(store) -> None:
    sender = Sender()
    sender.fail_for.add(CHAT)
    clock = FixedClock(TEN)
    await _tick(store, clock, sender)
    assert sender.sent == []
    sender.fail_for.clear()
    await _tick(store, clock, sender)
    assert len(sender.sent) == 1


async def test_failure_in_one_chat_does_not_stop_others(store) -> None:
    store.enable_chat(OTHER, NINE, TZ)
    sender = Sender()
    sender.fail_for.add(min(CHAT, OTHER))
    await _tick(store, FixedClock(TEN), sender)
    assert [chat for chat, _ in sender.sent] == [max(CHAT, OTHER)]


async def test_chats_are_counted_separately(store) -> None:
    store.enable_chat(OTHER, NINE, TZ)
    sender = Sender()
    await _tick(store, FixedClock(TEN), sender)
    assert {chat for chat, _ in sender.sent} == {CHAT, OTHER}


async def test_chat_not_set_up_gets_nothing(tmp_path) -> None:
    store = Store(tmp_path / "p.db")
    sender = Sender()
    await _tick(store, FixedClock(TEN), sender)
    assert sender.sent == []
    store.close()


async def test_migrated_chat_gets_the_post_at_its_new_id(store) -> None:
    new_chat = -100999

    class Moved(Sender):
        async def send_text(self, chat_id, text) -> None:
            if chat_id == CHAT:
                raise TelegramMigrateToChat(SendMessage(chat_id=chat_id, text="x"), "up", new_chat)
            await super().send_text(chat_id, text)

    sender = Moved()
    clock = FixedClock(TEN)
    await _tick(store, clock, sender)
    await _tick(store, clock, sender)
    assert [chat for chat, _ in sender.sent] == [new_chat]


def test_personal_fortune_is_a_separate_limit(store) -> None:
    clock = FixedClock(TEN)
    with store.fortune_transaction():
        store.save_fortune_issue(CHAT, 0, "2026-11-02", "other", "daily")
    chat = store.get_chat(CHAT)
    assert fortune.draw(store, clock, random.Random(1), LINES, chat, 1) == texts.render_fortune(
        LINES[0]
    )


async def test_failed_arrivals_pass_does_not_stop_the_fortune(store, monkeypatch) -> None:
    async def broken(*args) -> None:
        raise RuntimeError("arrivals are down")

    monkeypatch.setattr(app.arrivals, "tick", broken)
    sender = Sender()
    await app._scheduler_pass(store, FixedClock(TEN), random.Random(1), sender, CONTENT)
    assert len(sender.sent) == 1


async def test_failed_fortune_pass_does_not_stop_arrivals(store, monkeypatch) -> None:
    called = []

    async def broken(*args) -> None:
        raise RuntimeError("fortunes are down")

    async def arrivals_tick(*args) -> None:
        called.append(True)

    monkeypatch.setattr(app.fortunes, "tick", broken)
    monkeypatch.setattr(app.arrivals, "tick", arrivals_tick)
    await app._scheduler_pass(store, FixedClock(TEN), random.Random(1), Sender(), CONTENT)
    assert called == [True]
