"""One in-memory setup invitation per addition; no Telegram dependencies."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from pigeon.repo.store import Store

log = logging.getLogger("pigeon.setup_invitation")
DELAY_SECONDS = 5


class SetupInvitations:
    def __init__(self, store: Store, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.store = store
        self.sleep = sleep
        self.pending: dict[int, asyncio.Task] = {}
        self.tasks: set[asyncio.Task] = set()
        self.seen: set[tuple[int, int, int]] = set()
        self.migrated: set[int] = set()
        self.closed = False

    def membership(
        self,
        chat_id: int,
        chat_type: str,
        old: str,
        new: str,
        update_id: int,
        send: Callable[[], Awaitable[object]],
        *,
        event_date: int = 0,
    ) -> None:
        if self.closed or chat_type not in {"group", "supergroup"}:
            return
        key = (chat_id, event_date, update_id)
        if key in self.seen:
            return
        self.seen.add(key)
        if new in {"left", "kicked"}:
            self.cancel(chat_id)
            self.migrated.discard(chat_id)
        if (
            old not in {"left", "kicked"}
            or new not in {"member", "administrator"}
            or chat_id in self.pending
            or chat_id in self.migrated
            or self.store.get_chat(chat_id) is not None
        ):
            return
        task = asyncio.create_task(self._deliver(chat_id, send))
        self.pending[chat_id] = task
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _deliver(self, chat_id: int, send: Callable[[], Awaitable[object]]) -> None:
        try:
            await self.sleep(DELAY_SECONDS)
            if self.store.get_chat(chat_id) is None:
                await send()
        except Exception:
            log.warning("chat %s: setup invitation failed", chat_id, exc_info=True)
        finally:
            if self.pending.get(chat_id) is asyncio.current_task():
                self.pending.pop(chat_id, None)

    def cancel(self, chat_id: int) -> None:
        task = self.pending.pop(chat_id, None)
        if task is not None:
            task.cancel()

    def migration(self, old_chat_id: int, new_chat_id: int) -> None:
        self.migrated.update((old_chat_id, new_chat_id))
        self.cancel(old_chat_id)
        self.cancel(new_chat_id)

    async def close(self) -> None:
        self.closed = True
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()
