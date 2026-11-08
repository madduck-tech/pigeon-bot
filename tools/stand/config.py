"""Local stand settings: the test bot token and the ids of the test group and its members.

They live only in a git-ignored `.env.stand` or in `PIGEON_STAND_*` environment variables.
The environment wins over the file, so a one-off run can override a single value.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

PREFIX = "PIGEON_STAND_"
ENV_FILE = Path(".env.stand")
EXAMPLE_FILE = ".env.stand.example"
RUNS_DIR = Path("stand-runs")

# name -> what it is, for the error message.
REQUIRED = {
    "TOKEN": "token of the test bot from @BotFather",
    "CHAT_ID": "id of the test group, for example -1001234567890",
    "ADMIN_ID": "user_id of a group admin (the stand owner)",
    "MEMBER_ID": "user_id of a plain member: a second bot without admin rights",
}
INTEGERS = ("CHAT_ID", "ADMIN_ID", "MEMBER_ID")


class StandConfigError(Exception):
    """The stand cannot start: the message says what to fix."""


@dataclass(frozen=True)
class StandConfig:
    token: str = field(repr=False)
    chat_id: int
    admin_id: int
    member_id: int
    runs_dir: Path = RUNS_DIR


def read_env_file(path: Path) -> dict[str, str]:
    """`KEY=VALUE` lines; blank lines, `#` comments and `export ` prefixes are ignored."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_config(
    environ: Mapping[str, str] | None = None, env_file: Path | None = ENV_FILE
) -> StandConfig:
    environ = os.environ if environ is None else environ
    values: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        values.update(read_env_file(env_file))
    values.update({k: v for k, v in environ.items() if k.startswith(PREFIX)})

    missing = [
        f"  {PREFIX}{name}: {hint}"
        for name, hint in REQUIRED.items()
        if not values.get(PREFIX + name, "").strip()
    ]
    if missing:
        where = f"{env_file} or the environment" if env_file is not None else "the environment"
        raise StandConfigError(
            f"The test stand is not configured: set these in {where}.\n"
            + "\n".join(missing)
            + f"\nCopy {EXAMPLE_FILE} to {ENV_FILE} and see docs/operations.md, «Тестовый стенд»."
        )

    ids: dict[str, int] = {}
    for name in INTEGERS:
        raw = values[PREFIX + name].strip()
        try:
            ids[name] = int(raw)
        except ValueError:
            raise StandConfigError(f"{PREFIX}{name} must be an integer id, got {raw!r}") from None

    runs_dir = values.get(PREFIX + "RUNS_DIR", "").strip()
    return StandConfig(
        token=values[PREFIX + "TOKEN"].strip(),
        chat_id=ids["CHAT_ID"],
        admin_id=ids["ADMIN_ID"],
        member_id=ids["MEMBER_ID"],
        runs_dir=Path(runs_dir) if runs_dir else RUNS_DIR,
    )
