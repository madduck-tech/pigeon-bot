"""The /pigeon <sub> commands of the current main, with real permission checks."""

from __future__ import annotations

from aiogram.methods import GetMyCommands
from aiogram.types import BotCommandScopeAllChatAdministrators, BotCommandScopeAllGroupChats

from pigeon.render import texts
from tools.stand.harness import ADMIN_STATUSES, Stand

DESCRIPTION = (
    "/pigeon setup by the admin, /pigeon stats and /pigeon by a member, /pigeon arrive refused "
    "to the member and run by the admin, a spit tap, /pigeon quiet by an anonymous admin, "
    "getMyCommands for both group scopes (observed, not checked)"
)
TIMEZONE = "Europe/Moscow"
REFUSALS = (texts.NOT_GROUP, texts.NOT_ENABLED, texts.ADMIN_ONLY)
SCOPES = (BotCommandScopeAllGroupChats(), BotCommandScopeAllChatAdministrators())


def scope_name(scope) -> str:
    return getattr(scope.type, "value", scope.type)


async def run(stand: Stand) -> None:
    await common_flow(
        stand,
        setup=f"/pigeon setup {TIMEZONE}",
        stats="/pigeon stats",
        status="/pigeon",
        arrive="/pigeon arrive",
        quiet="/pigeon quiet 23 8",
    )
    # main publishes no menu, so whatever is there is left by someone else: report it only.
    for scope in SCOPES:
        step, _ = await stand.call(
            f"getMyCommands {scope_name(scope)} (observed)", GetMyCommands(scope=scope)
        )
        step.check("Telegram answered getMyCommands", step.error is None, step.error)


async def common_flow(
    stand: Stand, *, setup: str, stats: str, status: str, arrive: str, quiet: str
) -> None:
    """The path both command sets share: setup, stats, status, a refused and a real arrival,
    a spit tap and an anonymous admin command."""
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
