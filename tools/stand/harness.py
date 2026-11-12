"""The stand: the bot of this working tree on a test token, fed synthetic updates.

Wiring mirrors `pigeon.app.run_bot` without long polling and the scheduler. Outgoing calls
go to the real Bot API through the Recorder, which writes them into the report.
"""

from __future__ import annotations

import itertools
import json
import random
import subprocess
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import TelegramMethod
from aiogram.types import Update

import pigeon
from pigeon.app import build_bot
from pigeon.clock import SystemClock
from pigeon.content import load_content
from pigeon.handlers.pigeon import build_router
from pigeon.repo.store import Store
from tools.stand.config import StandConfig
from tools.stand.recorder import Recorder, StandGuardError, plain

REPO = Path(__file__).resolve().parents[2]
# src/pigeon/__init__.py -> the working tree the bot code is imported from.
PIGEON_TREE = Path(pigeon.__file__).resolve().parents[2]
PROD_USERNAMES = frozenset({"golubinskity_bot"})
ADMIN_STATUSES = ("creator", "administrator")
# Telegram's service account behind every anonymous admin message.
ANONYMOUS_ADMIN = {
    "id": 1087968824,
    "is_bot": True,
    "first_name": "Group",
    "username": "GroupAnonymousBot",
}
SYNTHETIC_MESSAGE_BASE = 2_000_000_000


class ProdBotError(Exception):
    """The token belongs to the production bot."""


@dataclass(frozen=True)
class Actor:
    """Who sends a synthetic update. An anonymous admin posts as the chat itself."""

    role: str
    user: dict[str, Any]
    as_chat: bool = False


@dataclass
class Step:
    title: str
    kind: str
    update: dict[str, Any] | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    handled: bool | None = None
    error: str | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)

    # --- lookups -------------------------------------------------------------

    def calls_of(self, method: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == method]

    def sent(self) -> list[dict[str, Any]]:
        return [c for c in self.calls_of("sendMessage") if c.get("ok")]

    # --- checks --------------------------------------------------------------

    def check(self, description: str, ok: bool, detail: Any = None) -> bool:
        entry: dict[str, Any] = {"check": description, "ok": bool(ok)}
        if detail is not None:
            entry["detail"] = detail
        self.checks.append(entry)
        return bool(ok)

    def expect_handled(self) -> bool:
        return self.check(
            "the update reached a handler and it finished",
            self.handled is True and self.error is None,
            self.error,
        )

    def expect_sent(self, text: str) -> dict[str, Any] | None:
        """Telegram accepted a sendMessage with exactly this text."""
        match = next((c for c in self.sent() if c["params"].get("text") == text), None)
        self.check(f"Telegram accepted sendMessage {text!r}", match is not None)
        return match

    def expect_sent_except(self, *refusals: str) -> dict[str, Any] | None:
        """Telegram accepted a sendMessage that is none of the given refusals."""
        sent = self.sent()
        match = next((c for c in sent if c["params"].get("text") not in refusals), None)
        self.check(
            "Telegram accepted a sendMessage that is not a refusal",
            match is not None and len(sent) == 1,
            [c["params"].get("text") for c in sent],
        )
        return match

    def expect_member_lookup(self, user_id: int, statuses: tuple[str, ...]) -> None:
        """The bot asked Telegram about this user and got one of these statuses."""
        lookups = [c for c in self.calls_of("getChatMember") if c["params"]["user_id"] == user_id]
        status = lookups[0].get("result", {}).get("status") if lookups else None
        self.check(
            f"getChatMember({user_id}) answered by Telegram with one of {statuses}",
            len(lookups) == 1 and lookups[0].get("ok") and status in statuses,
            status,
        )

    def expect_no_call(self, method: str) -> None:
        self.check(f"the bot made no {method} call", not self.calls_of(method))

    def find_button(self, prefix: str) -> tuple[dict[str, Any], str] | None:
        """(sent message, callback data) of the first accepted button starting with prefix."""
        for call in self.sent():
            markup = call["params"].get("reply_markup") or {}
            for row in markup.get("inline_keyboard", []):
                for button in row:
                    data = button.get("callback_data", "")
                    if data.startswith(prefix):
                        return call["result"], data
        return None

    def expect_button(self, prefix: str) -> tuple[dict[str, Any], str] | None:
        found = self.find_button(prefix)
        self.check(f"Telegram accepted a message with a {prefix}… button", found is not None)
        return found

    def expect_no_button(self, prefix: str) -> None:
        self.check(f"no message with a {prefix}… button was sent", self.find_button(prefix) is None)

    def expect_expected_answer_error(self) -> None:
        answers = self.calls_of("answerCallbackQuery")
        self.check(
            "answerCallbackQuery was rejected by Telegram as expected for a synthetic query",
            len(answers) == 1 and "expected_error" in answers[0],
            [a.get("error") for a in answers],
        )

    def expect_edit(self, message_id: int) -> None:
        edits = [
            c
            for c in self.calls_of("editMessageText")
            if c.get("ok") and c["params"].get("message_id") == message_id
        ]
        self.check(f"Telegram accepted editMessageText of message {message_id}", len(edits) == 1)

    @property
    def passed(self) -> bool:
        return self.error is None and all(c["ok"] for c in self.checks)

    def to_json(self) -> dict[str, Any]:
        return {
            "step": self.title,
            "kind": self.kind,
            "passed": self.passed,
            "handled": self.handled,
            "error": self.error,
            "update": self.update,
            "calls": self.calls,
            "checks": self.checks,
        }


class Stand:
    def __init__(self, config: StandConfig, bot: Bot, run_dir: Path, scenario: str) -> None:
        self.config = config
        self.bot = bot
        self.run_dir = run_dir
        self.scenario = scenario
        self.recorder = Recorder(config.chat_id)
        bot.session.middleware(self.recorder)
        self.db_path = run_dir / "stand.db"
        self.store: Store | None = None
        self.dispatcher = Dispatcher()
        self.steps: list[Step] = []
        self.extra: dict[str, Any] = {}  # scenario observations, such as model timings
        self.me: dict[str, Any] | None = None
        self.chat: dict[str, Any] | None = None
        self._update_ids = itertools.count(1)
        self._message_ids = itertools.count(SYNTHETIC_MESSAGE_BASE)
        self.admin = Actor("admin", {"id": config.admin_id, "is_bot": False, "first_name": "Админ"})
        self.member = Actor(
            "member", {"id": config.member_id, "is_bot": True, "first_name": "Гость"}
        )
        self.anonymous_admin = Actor("anonymous admin", ANONYMOUS_ADMIN, as_chat=True)

    # --- lifecycle -----------------------------------------------------------

    async def open(self) -> None:
        """Prove the token is not production's, then build the bot like run_bot does."""
        step = self.begin("getMe: refuse the production bot", "guard")
        me = await self.bot.get_me()
        self.me = plain(me)
        if (me.username or "").lower() in PROD_USERNAMES:
            step.check("the token is not the production bot", False, me.username)
            raise ProdBotError(f"refusing to run: the token belongs to @{me.username}")
        step.check("the token is not the production bot", True)
        self.recorder.verified = True

        step = self.begin("getChatMember: the bot is in the stand chat", "guard")
        chat_type = chat_type_of(self.config.chat_id)
        if chat_type is None:
            raise StandGuardError(f"chat id {self.config.chat_id} is not a group")
        if self.scenario == "forbidden":
            # Only this adverse scenario accepts an absent bot, still in the configured group.
            try:
                member = await self.bot.get_chat_member(self.config.chat_id, me.id)
            except TelegramForbiddenError:
                step.calls[-1]["expected_error"] = "removed test bot membership probe"
                step.check("Telegram refused the removed bot membership probe", True)
            else:
                removed = member.status in ("left", "kicked")
                step.check("the test bot was removed from the stand chat", removed, member.status)
                if not removed:
                    raise StandGuardError("forbidden scenario requires the test bot to be removed")
        else:
            member = await self.bot.get_chat_member(self.config.chat_id, me.id)
            step.check(
                "the bot is a member of the stand chat",
                member.status not in ("left", "kicked"),
                member.status,
            )
            if member.status in ("left", "kicked"):
                raise StandGuardError("the test bot is not in the stand chat")
        self.chat = {"id": self.config.chat_id, "type": chat_type}

        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.db_path)
        rng = random.Random()
        content = load_content(REPO / "content")
        self.dispatcher.include_router(build_router(self.store, SystemClock(), content, rng))

    async def close(self) -> None:
        if self.store is not None:
            self.store.close()
        await self.bot.session.close()

    # --- steps ---------------------------------------------------------------

    def begin(self, title: str, kind: str) -> Step:
        step = Step(title, kind)
        self.steps.append(step)
        self.recorder.calls = step.calls
        return step

    async def command(self, title: str, actor: Actor, text: str) -> Step:
        """A synthetic group message from actor, fed to the dispatcher."""
        message: dict[str, Any] = {
            "message_id": next(self._message_ids),
            "date": int(time.time()),
            "chat": self.chat,
            "from": actor.user,
            "text": text,
        }
        if text.startswith("/"):
            command = text.split()[0]
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(command)}]
        if actor.as_chat:
            message["sender_chat"] = self.chat
        return await self._feed(title, "command", actor, {"message": message})

    async def tap(self, title: str, actor: Actor, message: dict[str, Any], data: str) -> Step:
        """A synthetic button tap on a real message the bot sent to the stand chat."""
        query_id = f"stand-{uuid.uuid4().hex}"
        self.recorder.synthetic_queries.add(query_id)
        query = {
            "id": query_id,
            "from": actor.user,
            "chat_instance": "stand",
            "message": message,
            "data": data,
        }
        return await self._feed(title, "callback_query", actor, {"callback_query": query})

    async def call(self, title: str, method: TelegramMethod) -> tuple[Step, Any]:
        """A Bot API call made by the harness itself, for example getMyCommands."""
        step = self.begin(title, "api")
        result = None
        try:
            result = await self.bot(method)
            step.handled = True
        except Exception as exc:  # recorded, the scenario decides what follows
            step.error = f"{type(exc).__name__}: {exc}"
        return step, result

    async def run(self, title: str, action: Callable[[], Awaitable[Any]]) -> Step:
        """Bot code the harness runs directly, such as what run_bot does at startup."""
        step = self.begin(title, "startup")
        try:
            await action()
            step.handled = True
        except Exception as exc:
            step.error = f"{type(exc).__name__}: {exc}"
        return step

    async def _feed(self, title: str, kind: str, actor: Actor, payload: dict) -> Step:
        step = self.begin(title, kind)
        data = {"update_id": next(self._update_ids), **payload}
        step.update = {"actor": actor.role, **data}
        try:
            update = Update.model_validate(data, context={"bot": self.bot})
            response = await self.dispatcher.feed_update(self.bot, update)
            step.handled = response is not UNHANDLED
        except Exception as exc:
            step.handled = True
            step.error = f"{type(exc).__name__}: {exc}"
        return step

    # --- report --------------------------------------------------------------

    def global_checks(self) -> list[dict[str, Any]]:
        calls = [c for s in self.steps for c in s.calls]
        unexpected = [
            {"method": c["method"], "error": c.get("error") or c.get("blocked")}
            for c in calls
            if not c.get("ok") and "expected_error" not in c
        ]
        chats = {
            c["params"][k] for c in calls for k in ("chat_id", "from_chat_id") if k in c["params"]
        }
        return [
            {
                "check": "the bot code and the harness come from the same working tree",
                "ok": PIGEON_TREE == REPO,
                "detail": None if PIGEON_TREE == REPO else "pigeon is imported from elsewhere",
            },
            {
                "check": "every Bot API call succeeded or failed only as expected",
                "ok": not unexpected,
                "detail": unexpected or None,
            },
            {
                "check": "every call went to the stand chat",
                "ok": chats <= {self.config.chat_id},
                "detail": sorted(chats),
            },
        ]

    def report(self, error: str | None = None) -> dict[str, Any]:
        global_checks = self.global_checks()
        passed = (
            error is None
            and bool(self.steps)
            and all(s.passed for s in self.steps)
            and all(c["ok"] for c in global_checks)
        )
        return {
            "scenario": self.scenario,
            "passed": passed,
            "error": error,
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "revision": revision(),
            "bot": self.me,
            "chat": self.chat,
            "actors": {
                a.role: a.user["id"] for a in (self.admin, self.member, self.anonymous_admin)
            },
            "database": str(self.db_path),
            "summary": {
                "steps": len(self.steps),
                "calls": sum(len(s.calls) for s in self.steps),
                "failed_steps": [s.title for s in self.steps if not s.passed],
                # A burst the stand waited out would break a handler in production.
                "flood_waits": [
                    {"step": s.title, "method": c["method"], "seconds": c["flood_waits"]}
                    for s in self.steps
                    for c in s.calls
                    if c.get("flood_waits")
                ],
            },
            "global_checks": global_checks,
            "observations": self.extra,
            "steps": [s.to_json() for s in self.steps],
        }

    def write_report(self, report: dict[str, Any]) -> Path:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        text = json.dumps(report, ensure_ascii=False, indent=2)
        for secret in self.config.secrets:  # never write a secret, whatever carried it in
            text = text.replace(secret, "<token>")
        path = self.run_dir / "report.json"
        path.write_text(text + "\n", encoding="utf-8")
        public = json.dumps(redact(json.loads(text), self._private_ids()), ensure_ascii=False)
        (self.run_dir / "report.public.json").write_text(public + "\n", encoding="utf-8")
        return path

    def _private_ids(self) -> dict[int, str]:
        ids = {
            self.config.chat_id: "<stand_chat_id>",
            self.config.admin_id: "<stand_admin_id>",
            self.config.member_id: "<stand_member_id>",
        }
        if self.me:
            ids[self.me["id"]] = "<stand_bot_id>"
        return ids


NAME_KEYS = frozenset({"first_name", "last_name", "username", "title", "custom_title", "database"})


def redact(value: Any, ids: dict[int, str], key: str | None = None) -> Any:
    """Stand ids, names and paths -> placeholders, for a report that can go into an Issue.

    Structural, not textual: bot texts stay intact even when a name is a common word.
    """
    if isinstance(value, dict):
        return {k: redact(v, ids, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, ids, key) for v in value]
    if key in NAME_KEYS and isinstance(value, str):
        return f"<{key}>"
    if isinstance(value, int) and not isinstance(value, bool) and value in ids:
        return ids[value]
    if isinstance(value, str):
        for raw, placeholder in sorted(ids.items(), key=lambda kv: -len(str(kv[0]))):
            value = value.replace(str(raw), placeholder)
    return value


def chat_type_of(chat_id: int) -> str | None:
    """Bot API ids: supergroups are -100…, basic groups other negatives, users positive."""
    if str(chat_id).startswith("-100"):
        return "supergroup"
    return "group" if chat_id < 0 else None


def _git(tree: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=tree, capture_output=True, text=True, check=False
        )
    except OSError:  # no git, or the tree is gone
        return ""
    return result.stdout.strip()


def revision() -> dict[str, Any]:
    """The revision of the bot code that actually ran, and whether the harness is from it.

    A shared virtualenv can import pigeon from another working tree than tools/.
    """
    bot_tree = PIGEON_TREE
    return {
        "commit": _git(bot_tree, "rev-parse", "HEAD") or None,
        "dirty": bool(_git(bot_tree, "status", "--porcelain")),
        "same_tree_as_harness": bot_tree == REPO,
        "harness_commit": _git(REPO, "rev-parse", "HEAD") or None,
    }


def scrub(value: Any, *secrets: str) -> Any:
    """A copy of value with every secret cut out of every string."""
    if isinstance(value, dict):
        return {k: scrub(v, *secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, *secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret and secret in value:
                value = value.replace(secret, "<token>")
    return value


def new_bot(config: StandConfig) -> Bot:
    return build_bot(config.token)
