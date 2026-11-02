"""YAML content: texts that change without code edits. Loaded once at startup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from pigeon.services.achievements import CODES

MAX_CALLBACK_DATA = 64  # bytes, Telegram's limit


@dataclass(frozen=True)
class Achievement:
    name: str
    description: str


@dataclass(frozen=True)
class ShooTexts:
    actions: tuple[str, ...]
    stayed: tuple[str, ...]
    flew: tuple[str, ...]


@dataclass(frozen=True)
class Content:
    fortunes: tuple[str, ...]
    achievements: dict[str, Achievement]
    names: tuple[str, ...]
    arrivals: tuple[str, ...]
    shoos: ShooTexts
    timezones: dict[str, str]


def _read(directory: Path, name: str):
    return yaml.safe_load((directory / name).read_text(encoding="utf-8"))


def _lines(raw, label: str) -> tuple[str, ...]:
    if not raw or not all(isinstance(line, str) for line in raw):
        raise ValueError(f"{label} must be a non-empty list of strings")
    return tuple(raw)


def _arrivals(raw) -> tuple[str, ...]:
    phrases = _lines(raw, "arrivals.yaml")
    for phrase in phrases:
        if phrase.format(name="") == phrase:
            raise ValueError(f"arrivals.yaml phrase has no {{name}}: {phrase}")
    return phrases


def _timezones(raw) -> dict[str, str]:
    if not raw or not isinstance(raw, dict):
        raise ValueError("timezones.yaml must be a non-empty mapping")
    for name in raw.values():
        ZoneInfo(name)  # a wrong name stops the bot here, not at an admin's tap
        if len(f"pg:tz:{name}".encode()) > MAX_CALLBACK_DATA:
            raise ValueError(f"timezones.yaml name is too long for a button: {name}")
    return dict(raw)


def load_content(directory: Path) -> Content:
    """Read content/*.yaml. Fails at startup, not at a user's tap, if a file is broken."""
    fortunes = _lines(_read(directory, "fortunes.yaml"), "fortunes.yaml")
    raw = _read(directory, "achievements.yaml")
    missing = set(CODES) - set(raw or {})
    if missing:
        raise ValueError(f"achievements.yaml has no text for: {sorted(missing)}")
    achievements = {code: Achievement(**raw[code]) for code in CODES}
    names = _lines(_read(directory, "names.yaml"), "names.yaml")
    if len(set(names)) != len(names):
        raise ValueError("names.yaml must hold unique names")
    shoos = _read(directory, "shoos.yaml") or {}
    return Content(
        fortunes=fortunes,
        achievements=achievements,
        names=names,
        arrivals=_arrivals(_read(directory, "arrivals.yaml")),
        shoos=ShooTexts(
            actions=_lines(shoos.get("actions"), "shoos.yaml actions"),
            stayed=_lines(shoos.get("stayed"), "shoos.yaml stayed"),
            flew=_lines(shoos.get("flew"), "shoos.yaml flew"),
        ),
        timezones=_timezones(_read(directory, "timezones.yaml")),
    )
