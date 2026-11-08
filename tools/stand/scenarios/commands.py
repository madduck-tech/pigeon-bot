"""The separate commands of ADR-0013 (PR #21): the smoke of PR #21 run by an agent.

Needs a revision with `pigeon.telegram.commands`; on an older one the scenario fails at once.
"""

from __future__ import annotations

from aiogram.methods import DeleteMyCommands, GetMyCommands

from tools.stand.harness import Stand
from tools.stand.scenarios.smoke import SCOPES, TIMEZONE, common_flow, scope_name

DESCRIPTION = (
    "startup menus, /setup by the admin, /stats and /pigeon stats by a member, /arrive refused "
    "to the member and run by the admin, a spit tap, /quiet by an anonymous admin, "
    "getMyCommands for both group scopes compared with the menus of this revision"
)


async def run(stand: Stand) -> None:
    try:
        from pigeon.telegram.commands import menus, publish_menus
    except ImportError as exc:
        step = stand.begin("startup: publish the command menus", "startup")
        step.error = f"this revision has no command menus (ADR-0013, PR #21): {exc}"
        return

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
