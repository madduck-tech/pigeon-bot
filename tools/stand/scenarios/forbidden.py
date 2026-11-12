"""Real Bot API rejection after the operator removes the test bot from its test group.

Only fixture databases change. The recipient remains the configured stand group; this scenario
never removes a bot, changes membership, or relaxes the recipient/production guards itself.
"""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, timedelta

from pigeon.clock import FixedClock
from pigeon.content import load_content
from pigeon.repo.store import Store
from pigeon.scheduler import arrivals, fortunes
from pigeon.telegram.messages import TelegramArrivalSender
from tools.stand.harness import REPO, Stand

DESCRIPTION = (
    "removed test bot: real Forbidden on arrivals, expired arrival edits, daily and Monday "
    "posts; chat disabled, one warning without traceback, no request on the next pass"
)


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


async def run(stand: Stand) -> None:
    content = load_content(REPO / "content")
    chat_id = stand.config.chat_id
    # Tuesday and Monday, after morning/quiet hours, independent of the operator's wall clock.
    for case in ("arrival", "expiry", "daily", "monday"):
        store = Store(stand.run_dir / f"{case}.db")
        now = datetime(2026, 11, 9 if case == "monday" else 10, 12, tzinfo=UTC)
        clock = FixedClock(now)
        rng = random.Random(25)
        sender = TelegramArrivalSender(stand.bot, store, clock, content.quests)
        store.enable_chat(chat_id, now, "UTC")
        if case == "arrival":
            store.save_plan(chat_id, now.date().isoformat(), now, "pending")
        elif case == "expiry":
            event_id = store.open_event(
                chat_id,
                now - timedelta(minutes=20),
                now - timedelta(minutes=5),
                "auto",
                "Гена",
                "Гена",
            )
            store.set_event_message(event_id, 1)
        elif case == "monday":
            store.claim_chronicle(chat_id, now.date().isoformat(), "pending", now)
            store.finish_chronicle(
                chat_id, now.date().isoformat(), "Гена вспоминал прошлую неделю."
            )

        async def tick(case=case, store=store, clock=clock, rng=rng, sender=sender):
            if case in ("arrival", "expiry"):
                await arrivals.tick(store, clock, rng, sender, content)
            else:
                await fortunes.tick(
                    store, clock, rng, sender, content.fortunes, content.names, content.quests
                )

        capture = Capture()
        logger = logging.getLogger(
            "pigeon.scheduler" if case in ("arrival", "expiry") else "pigeon.fortunes"
        )
        logger.addHandler(capture)
        try:
            step = await stand.run(f"{case}: real rejection from the removed group", tick)
            step.expect_handled()
            expected_method = "editMessageText" if case == "expiry" else "sendMessage"
            calls = step.calls_of(expected_method)
            forbidden = [
                c
                for c in calls
                if not c.get("ok") and c.get("error_type") == "TelegramForbiddenError"
            ]
            step.check(
                "real Telegram Forbidden reached the affected operation",
                len(forbidden) == 1,
                [c.get("error") for c in calls],
            )
            for call in forbidden:
                call["expected_error"] = "removed test bot: real Telegram Forbidden"
            step.check("the production tick disabled this chat", not store.is_enabled(chat_id))
            step.check(
                "the disabled chat is excluded from scheduler reads", not store.enabled_chats()
            )
            warnings = [r for r in capture.records if r.levelno == logging.WARNING]
            step.check(
                "one warning without traceback",
                len(warnings) == 1 and warnings[0].exc_info is None,
                [r.getMessage() for r in capture.records],
            )
            step.check(
                "no error or traceback record",
                all(r.levelno < logging.ERROR and not r.exc_info for r in capture.records),
            )
            if case == "monday":
                issue = store.get_fortune_issue(chat_id, 0, now.date().isoformat())
                step.check(
                    "the rejected Monday post contains the stored chronicle",
                    issue is not None and "Гена вспоминал" in issue.rendered_text,
                )
            capture.records.clear()
            step = await stand.run(f"{case}: next pass skips the disabled group", tick)
            step.expect_handled()
            step.check("no further Bot API call or warning", not step.calls and not capture.records)
            step.check("the persisted chat stays disabled", not store.is_enabled(chat_id))
        finally:
            logger.removeHandler(capture)
            store.close()
