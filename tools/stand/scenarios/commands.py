"""The separate commands of ADR-0013 and their menus: the smoke of PR #21, run by an agent."""

from __future__ import annotations

from aiogram.methods import DeleteMyCommands, GetMyCommands
from aiogram.types import BotCommandScopeAllChatAdministrators, BotCommandScopeAllGroupChats

from pigeon.render import texts
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

    await common_flow(
        stand,
        setup=f"/setup {TIMEZONE}",
        stats="/stats",
        status="/pigeon stats",
        arrive="/arrive",
        quiet="/quiet 23 8",
    )

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
