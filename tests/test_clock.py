from datetime import UTC, datetime, timedelta

import pytest

from pigeon.clock import FixedClock, SystemClock


def test_fixed_clock_returns_set_time_and_can_advance() -> None:
    start = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    clock = FixedClock(start)
    assert clock.now() == start
    clock.set(start + timedelta(hours=1))
    assert clock.now() == start + timedelta(hours=1)


def test_fixed_clock_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 11, 1, 9, 0))


def test_system_clock_returns_aware_datetime() -> None:
    assert SystemClock().now().tzinfo is not None
