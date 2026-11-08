"""`python -m tools.stand <scenario>`: run a scenario on the test stand and write its report.

Exit codes: 0 passed, 1 a check failed, 2 the stand refused to run (config, guard, network).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from tools.stand.config import StandConfig, StandConfigError, load_config
from tools.stand.harness import Stand, new_bot, scrub
from tools.stand.scenarios import SCENARIOS

EXIT_PASSED, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2


class _Redact(logging.Filter):
    """Keeps the token out of every log line, whatever logs it."""

    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if self.token in message:
            record.msg, record.args = message.replace(self.token, "<token>"), None
        return True


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tools.stand", description=__doc__)
    parser.add_argument("scenario", choices=sorted(SCENARIOS), help="scenario to run")
    parser.add_argument("--env-file", type=Path, default=Path(".env.stand"))
    return parser.parse_args(argv)


async def run_scenario(config: StandConfig, name: str, bot=None) -> tuple[int, Path | None]:
    run_dir = new_run_dir(config.runs_dir, name)
    stand = Stand(config, bot or new_bot(config), run_dir, name)
    code, error = EXIT_PASSED, None
    try:
        try:
            await stand.open()
        except Exception as exc:  # production bot, wrong chat, bad token, no network
            code, error = EXIT_REFUSED, f"{type(exc).__name__}: {exc}"
        else:
            try:
                await SCENARIOS[name].run(stand)
            except Exception as exc:  # a broken scenario fails the run, it is not a refusal
                code, error = EXIT_FAILED, f"{type(exc).__name__}: {exc}"
    finally:
        await stand.close()
    report = scrub(stand.report(error), config.token)
    if code == EXIT_PASSED and not report["passed"]:
        code = EXIT_FAILED
    path = stand.write_report(report)
    _print_summary(report, path)
    return code, path


def new_run_dir(runs_dir: Path, name: str) -> Path:
    """A folder no other run has: two runs started in the same second never share a base."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    while True:
        run_dir = runs_dir / f"{stamp}-{name}-{uuid.uuid4().hex[:6]}"
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return run_dir


def _print_summary(report: dict, path: Path) -> None:
    for step in report["steps"]:
        mark = "PASS" if step["passed"] else "FAIL"
        print(f"[{mark}] {step['step']}  ({len(step['calls'])} calls)")
        for check in step["checks"]:
            if not check["ok"]:
                print(f"       ✗ {check['check']}: {check.get('detail')}")
        if step["error"]:
            print(f"       ✗ error: {step['error']}")
    for check in report["global_checks"]:
        print(f"[{'PASS' if check['ok'] else 'FAIL'}] {check['check']}")
    for wait in report["summary"]["flood_waits"]:
        print(
            f"[WARN] flood control: {wait['method']} in «{wait['step']}» waited "
            f"{sum(wait['seconds'])} s; in production this call would have failed"
        )
    if report["error"]:
        print(f"refused: {report['error']}")
    print(f"{'PASSED' if report['passed'] else 'FAILED'}: {report['scenario']}; report {path}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(env_file=args.env_file)
    except StandConfigError as exc:
        print(exc, file=sys.stderr)
        return EXIT_REFUSED
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(_Redact(config.token))
    code, _ = asyncio.run(run_scenario(config, args.scenario))
    return code


if __name__ == "__main__":
    sys.exit(main())
