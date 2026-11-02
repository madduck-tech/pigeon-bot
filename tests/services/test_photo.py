from datetime import UTC, datetime, timedelta

import pytest

from pigeon.clock import FixedClock
from pigeon.repo.store import Store
from pigeon.services import photo

CHAT = -6001
NOON = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)  # 12:00 Moscow
NIGHT = datetime(2026, 11, 2, 22, 0, tzinfo=UTC)  # 01:00 Moscow


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "pigeon.db")
    s.enable_chat(CHAT, NOON, "Europe/Moscow")
    yield s
    s.close()


@pytest.fixture
def clock():
    return FixedClock(NOON)


@pytest.mark.parametrize(
    "caption",
    ["голубь", "Смотрите, ГОЛУБЬ!", "нашёл голубя", "кормим голубей", "вот 🕊", "вот 🕊️"],
)
def test_captions_about_a_pigeon(caption) -> None:
    assert photo.mentions_pigeon(caption)


@pytest.mark.parametrize("caption", [None, "", "ворона", "голубой вагон", "голубика", "Голубев"])
def test_captions_about_something_else(caption) -> None:
    assert not photo.mentions_pigeon(caption)


def test_one_poll_an_hour(store, clock) -> None:
    assert photo.claim(store, clock, CHAT)
    clock.set(NOON + photo.COOLDOWN - timedelta(seconds=1))
    assert not photo.claim(store, clock, CHAT)
    clock.set(NOON + photo.COOLDOWN)
    assert photo.claim(store, clock, CHAT)


def test_cooldown_is_per_chat(store, clock) -> None:
    other = -6002
    store.enable_chat(other, NOON, "Europe/Moscow")
    assert photo.claim(store, clock, CHAT)
    assert photo.claim(store, clock, other)


def test_caption_stays_quiet_at_night(store, clock) -> None:
    clock.set(NIGHT)
    assert not photo.claim_for_caption(store, clock, store.get_chat(CHAT), "голубь")
    assert photo.claim(store, clock, CHAT)  # and it did not use up the hour


def test_caption_stays_quiet_during_a_pause(store, clock) -> None:
    store.set_paused_until(CHAT, NOON + timedelta(hours=1))
    assert not photo.claim_for_caption(store, clock, store.get_chat(CHAT), "голубь")


def test_caption_shares_the_hour_with_the_command(store, clock) -> None:
    assert photo.claim_for_caption(store, clock, store.get_chat(CHAT), "голубь")
    assert not photo.claim(store, clock, CHAT)
