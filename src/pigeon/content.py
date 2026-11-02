"""YAML content: texts that change without code edits. Loaded once at startup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from pigeon.services.achievements import CODES

MAX_POLL_OPTION = 100  # Telegram's limit


@dataclass(frozen=True)
class Achievement:
    name: str
    description: str


@dataclass(frozen=True)
class Content:
    fortunes: tuple[str, ...]
    achievements: dict[str, Achievement]
    names: tuple[str, ...]


def load_content(directory: Path) -> Content:
    """Read content/*.yaml. Fails at startup, not at a user's tap, if a file is broken."""
    fortunes = yaml.safe_load((directory / "fortunes.yaml").read_text(encoding="utf-8"))
    if not fortunes or not all(isinstance(line, str) for line in fortunes):
        raise ValueError("fortunes.yaml must be a non-empty list of strings")
    raw = yaml.safe_load((directory / "achievements.yaml").read_text(encoding="utf-8"))
    missing = set(CODES) - set(raw or {})
    if missing:
        raise ValueError(f"achievements.yaml has no text for: {sorted(missing)}")
    achievements = {code: Achievement(**raw[code]) for code in CODES}
    names = yaml.safe_load((directory / "names.yaml").read_text(encoding="utf-8"))
    if not names or not all(isinstance(name, str) for name in names):
        raise ValueError("names.yaml must be a non-empty list of strings")
    if len(set(names)) != len(names) or max(map(len, names)) > MAX_POLL_OPTION:
        raise ValueError("names.yaml must hold unique names that fit a poll option")
    return Content(tuple(fortunes), achievements, tuple(names))
