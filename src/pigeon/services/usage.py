"""Best-effort usage collection, independent of Telegram and game transactions."""

import logging
from datetime import UTC

from pigeon.clock import Clock
from pigeon.repo.usage import UsageRepository

log = logging.getLogger("pigeon.usage")


class Usage:
    def __init__(self, repository: UsageRepository, clock: Clock) -> None:
        self.repository = repository
        self.clock = clock
        self._cleaned_day = None
        self._started = False
        self.maintain()

    def maintain(self) -> None:
        try:
            now = self.clock.now()
            if not self._started:
                self.repository.start(now)
                self._started = True
            day = now.astimezone(UTC).date()
            if self._cleaned_day != day:
                self.repository.cleanup(now)
                self._cleaned_day = day
        except Exception:
            log.warning("usage maintenance failed", exc_info=True)

    def record(self, chat_id: int, metric: str | None = None, user_id: int | None = None) -> None:
        try:
            self.repository.record(chat_id, self.clock.now(), metric, user_id)
        except Exception:
            log.warning("usage recording failed", exc_info=True)
