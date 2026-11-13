"""The separate commands of ADR-0013 and their menus: the smoke of PR #21, run by an agent."""

from __future__ import annotations

import asyncio
import time

from aiogram.methods import DeleteMyCommands, GetMyCommands
from aiogram.types import BotCommandScopeAllChatAdministrators, BotCommandScopeAllGroupChats

from pigeon.clock import SystemClock
from pigeon.render import texts
from pigeon.repo.usage import read_report
from tools.stand.harness import ADMIN_STATUSES, Stand

DESCRIPTION = (
    "startup menus, /setup by the admin, /stats and /pigeon with an argument by a member, "
    "/arrive refused to the member and run by the admin, a spit tap, /quiet by an anonymous "
    "admin, getMyCommands for both group scopes compared with the menus of this revision"
)
TIMEZONE = "Europe/Moscow"
REFUSALS = (texts.NOT_GROUP, texts.NOT_ENABLED, texts.ADMIN_ONLY)
SCOPES = (BotCommandScopeAllGroupChats(), BotCommandScopeAllChatAdministrators())


def scope_name(scope) -> str:
    return getattr(scope.type, "value", scope.type)


async def run(stand: Stand) -> None:
    # Imported here, not at the top, so offline tests can swap in a broken menu module.
    from pigeon.telegram.commands import menus, publish_menus

    # Menus outlive a run: without a reset, a menu left by an earlier run would pass the check.
    for scope in SCOPES:
        step, _ = await stand.call(
            f"reset the {scope_name(scope)} menu", DeleteMyCommands(scope=scope)
        )
        step.check("Telegram accepted deleteMyCommands", step.error is None, step.error)

    # run_bot publishes the menus before polling; the stand does the same.
    step = await stand.run("startup: publish the command menus", lambda: publish_menus(stand.bot))
    step.expect_handled()
    published = sorted(
        c["params"].get("scope", {}).get("type", "default")
        for c in step.calls_of("setMyCommands")
        if c.get("ok")
    )
    step.check(
        "Telegram accepted setMyCommands for both group scopes",
        published == sorted(scope_name(scope) for scope in SCOPES),
        published,
    )

    await invitation_flow(stand)

    await common_flow(
        stand,
        setup=f"/setup {TIMEZONE}",
        stats="/stats",
        status="/pigeon stats",
        arrive="/arrive",
        quiet="/quiet 23 8",
    )

    step = await stand.command("help discloses usage retention", stand.admin, "/help")
    step.expect_handled()
    step.expect_sent(texts.help_text())
    step.check(
        "help discloses analytics and delayed cleanup",
        "90 дней" in texts.help_text() and "откладывается" in texts.help_text(),
    )

    # A real SQLite failure during a command, followed by a real Bot API answer.
    conn = stand.store._conn
    conn.execute(
        "CREATE TEMP TRIGGER fail_usage BEFORE INSERT ON usage_counters "
        "BEGIN SELECT RAISE(FAIL, 'stand usage unavailable'); END"
    )
    try:
        step = await stand.command(
            "usage write failure does not block status", stand.admin, "/pigeon"
        )
        step.expect_handled()
        step.expect_sent_except(*REFUSALS)
    finally:
        conn.execute("DROP TRIGGER fail_usage")

    step = stand.begin("persisted usage from the Telegram flow", "assertion")
    report = read_report(stand.db_path, SystemClock().now())
    metrics = {}
    for row in report["days"]:
        if row["chat_id"] == stand.config.chat_id:
            for key, value in row["metrics"].items():
                metrics[key] = metrics.get(key, 0) + value
    step.check("both allowed and refused arrivals were counted", metrics.get("command.arrive") == 2)
    step.check("successful timezone save counted", metrics.get("setup.saved") == 2)
    step.check(
        "anonymous admin and bot member excluded from unique actors",
        report["participants"].get(stand.config.chat_id) == 1,
    )
    step.check(
        "synthetic callback rejection counted as real delivery failure",
        metrics.get("delivery.callback_error") == 3,
    )
    step.check(
        "failed analytics write did not invent an extra command count",
        metrics.get("command.pigeon") == 1,
    )
    step.check(
        "the real gameplay spit was persisted",
        sum(v for k, v in metrics.items() if k.startswith("hit.")) == 1,
    )
    stand.extra["usage_metrics"] = metrics

    expected = {
        scope.type: [{"command": c.command, "description": c.description} for c in commands]
        for scope, commands in menus()
    }
    for scope in SCOPES:
        step, result = await stand.call(
            f"getMyCommands {scope_name(scope)}", GetMyCommands(scope=scope)
        )
        got = [{"command": c.command, "description": c.description} for c in result or []]
        step.check(
            f"the {scope_name(scope)} menu in Telegram is the menu of this revision",
            step.error is None and got == expected[scope.type],
            {
                "expected": [c["command"] for c in expected[scope.type]],
                "got": [c["command"] for c in got],
            },
        )


async def common_flow(
    stand: Stand, *, setup: str, stats: str, status: str, arrive: str, quiet: str
) -> None:
    """Setup, stats, status, a refused and a real arrival, a spit tap and an anonymous admin
    command."""
    step = await stand.command("setup by the admin", stand.admin, setup)
    step.expect_handled()
    step.expect_member_lookup(stand.config.admin_id, ADMIN_STATUSES)
    step.expect_sent(texts.SETUP_DONE.format(tz=TIMEZONE))

    step = await stand.command("week stats by the member", stand.member, stats)
    step.expect_handled()
    step.expect_no_call("getChatMember")
    step.expect_sent_except(*REFUSALS)

    step = await stand.command("status by the member", stand.member, status)
    step.expect_handled()
    step.expect_no_call("getChatMember")
    step.expect_sent(texts.STATUS_AWAY)

    step = await stand.command("arrive refused to the member", stand.member, arrive)
    step.expect_handled()
    step.expect_member_lookup(stand.config.member_id, ("member",))
    step.expect_sent(texts.ADMIN_ONLY)
    step.expect_no_button("pg:")

    step = await stand.command("arrive by the admin", stand.admin, arrive)
    step.expect_handled()
    step.expect_member_lookup(stand.config.admin_id, ADMIN_STATUSES)
    arrival = step.expect_button("pg:hit:")

    if arrival is not None:
        message, data = arrival
        step = await stand.tap("spit by the member", stand.member, message, data)
        step.expect_handled()
        step.expect_expected_answer_error()
        answers = step.calls_of("answerCallbackQuery")
        answer = (answers[0]["params"].get("text") or "") if answers else ""
        step.check("the tap was answered with points, not a refusal", "+" in answer, answer)
        step.expect_edit(message["message_id"])

    step = await stand.command("quiet hours by an anonymous admin", stand.anonymous_admin, quiet)
    step.expect_handled()
    step.expect_no_call("getChatMember")
    step.expect_sent(texts.QUIET_SET.format(start=23, end=8))


async def invitation_flow(stand: Stand) -> None:
    """Real Bot API delivery after a synthetic addition; real permission lookups on taps."""
    started = time.monotonic()
    step = await stand.membership("new group: invite after addition", "left", "member")
    step.expect_handled()
    step.expect_no_call("sendMessage")
    await asyncio.sleep(5.5)
    sent = step.expect_sent(texts.SETUP_CHOOSE)
    step.check("one invitation after the five-second delay", len(step.sent()) == 1)
    step.check("five seconds elapsed", time.monotonic() - started >= 5)
    step.check(
        "invitation did not enable the chat", stand.store.get_chat(stand.config.chat_id) is None
    )
    button = step.expect_button("pg:tz:")
    if sent is not None and button is not None:
        message, data = button
        step = await stand.tap(
            "member cannot choose invitation timezone", stand.member, message, data
        )
        step.expect_handled()
        step.expect_member_lookup(stand.config.member_id, ("member",))
        step.expect_expected_answer_error()
        answers = step.calls_of("answerCallbackQuery")
        step.check(
            "member refused", bool(answers) and answers[0]["params"].get("text") == texts.ADMIN_ONLY
        )
        step.check(
            "refusal left chat unconfigured", stand.store.get_chat(stand.config.chat_id) is None
        )
        step = await stand.tap("admin chooses invitation timezone", stand.admin, message, data)
        step.expect_handled()
        step.expect_member_lookup(stand.config.admin_id, ADMIN_STATUSES)
        step.expect_expected_answer_error()
        step.expect_edit(message["message_id"])
        step.check("admin enabled chat", stand.store.is_enabled(stand.config.chat_id))

    step = await stand.membership("configured chat: removal", "member", "left")
    step.expect_handled()
    step.check("removal disabled chat", not stand.store.is_enabled(stand.config.chat_id))
    step = await stand.membership(
        "configured disabled chat: readdition stays silent", "left", "member"
    )
    step.expect_handled()
    await asyncio.sleep(5.5)
    step.expect_no_call("sendMessage")
    step.check("readdition did not enable chat", not stand.store.is_enabled(stand.config.chat_id))
