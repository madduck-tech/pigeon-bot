import random
from datetime import UTC, datetime, timedelta

import pytest

from pigeon.clock import FixedClock
from pigeon.repo.store import Store
from pigeon.services import fortune
from rigged import CONTENT

CHAT = -7001
TZ = "Europe/Moscow"
EVENING = datetime(2026, 11, 2, 20, 0, tzinfo=UTC)  # 23:00 Moscow
FORTUNES = CONTENT.fortunes


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "pigeon.db")
    s.enable_chat(CHAT, EVENING, TZ)
    yield s
    s.close()


def _draw(store, clock, user_id=1):
    return fortune.draw(store, clock, random.Random(1), FORTUNES, store.get_chat(CHAT), user_id)


def test_one_fortune_per_user_per_local_day(store) -> None:
    clock = FixedClock(EVENING)
    assert _draw(store, clock) is not None
    assert _draw(store, clock) is None
    assert _draw(store, clock, user_id=2) is not None


def test_new_local_day_comes_at_chat_midnight(store) -> None:
    clock = FixedClock(EVENING)
    _draw(store, clock)
    clock.set(EVENING + timedelta(minutes=59))  # 23:59 Moscow, the same day
    assert _draw(store, clock) is None
    clock.set(EVENING + timedelta(hours=1))  # 00:00 Moscow
    assert _draw(store, clock) is not None
