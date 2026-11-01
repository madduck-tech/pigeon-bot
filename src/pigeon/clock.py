"""Injectable clock. Services take a Clock instead of calling datetime.now() directly."""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Test double with a settable time."""

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._now = at

    def now(self) -> datetime:
        return self._now

    def set(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._now = at
