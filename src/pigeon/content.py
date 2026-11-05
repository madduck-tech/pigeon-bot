"""YAML content: texts that change without code edits. Loaded once at startup."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from string import Formatter
from zoneinfo import ZoneInfo

import yaml

from pigeon.services.achievements import CODES

MAX_CALLBACK_DATA = 64  # bytes, Telegram's limit


class FortuneFormat(StrEnum):
    CLASSIC = "classic"
    OMEN = "omen"
    SCENE = "scene"
    SPREAD = "spread"
    CHAT = "chat"
    SERIAL = "serial"


@dataclass(frozen=True)
class Fortune:
    id: str
    format: FortuneFormat
    text: str
    part: int | None = None
    next: str | None = None


def _fortunes(raw) -> tuple[Fortune, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("fortunes must be a non-empty list")
    result = []
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"id", "format", "text", "part", "next"}:
            raise ValueError("invalid fortune fields")
        try:
            ident, fmt, text = item["id"], FortuneFormat(item["format"]), item["text"]
            if not isinstance(ident, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", ident):
                raise ValueError("invalid fortune id")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty fortune text")
            fields = [
                (field, spec, conversion)
                for _, field, spec, conversion in Formatter().parse(text)
                if field is not None
            ]
            if fields != ([("name", "", None)] if fmt == FortuneFormat.SCENE else []):
                raise ValueError("invalid fortune placeholders")
            part, nxt = item.get("part"), item.get("next")
            if fmt == FortuneFormat.SERIAL:
                if (
                    type(part) is not int
                    or part < 1
                    or (nxt is not None and not isinstance(nxt, str))
                ):
                    raise ValueError("invalid serial")
            elif "part" in item or "next" in item:
                raise ValueError("serial fields on non-serial fortune")
            result.append(Fortune(ident, fmt, text, part, nxt))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid fortune: {exc}") from exc
    by_id = {f.id: f for f in result}
    if len(by_id) != len(result):
        raise ValueError("duplicate fortune id")
    visited = set()
    for root in (f for f in result if f.format == FortuneFormat.SERIAL and f.part == 1):
        current, count = root, 0
        while current:
            if (
                current.id in visited
                or current.part != count + 1
                or current.format != FortuneFormat.SERIAL
            ):
                raise ValueError("invalid serial chain")
            visited.add(current.id)
            count += 1
            if current.next and current.next not in by_id:
                raise ValueError("invalid serial next")
            current = by_id.get(current.next)
        if count not in (2, 3):
            raise ValueError("invalid serial length")
    if visited != {f.id for f in result if f.format == FortuneFormat.SERIAL}:
        raise ValueError("invalid serial chain")
    return tuple(result)


@dataclass(frozen=True)
class Quest:
    id: str
    name: str
    description: str
    target: int
    title: str


def _quests(raw) -> tuple[Quest, ...]:
    expected = {"trio-hit": 3, "team-scare": 3, "weekly-eight": 8, "hit-then-scare": 2}
    if not isinstance(raw, list):
        raise ValueError("quests must be a list")
    result = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {
            "id",
            "name",
            "description",
            "target",
            "title",
        }:
            raise ValueError("invalid quest fields")
        if (
            item["id"] not in expected
            or type(item["target"]) is not int
            or item["target"] != expected[item["id"]]
        ):
            raise ValueError("invalid quest id or target")
        if any(
            not isinstance(item[k], str) or not item[k].strip()
            for k in ("name", "description", "title")
        ):
            raise ValueError("invalid quest text")
        result.append(Quest(**item))
    if len(result) != 4 or {q.id for q in result} != set(expected):
        raise ValueError("quests must contain each quest exactly once")
    return tuple(result)


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
    fortunes: tuple[Fortune, ...]
    achievements: dict[str, Achievement]
    names: tuple[str, ...]
    arrivals: tuple[str, ...]
    shoos: ShooTexts
    timezones: dict[str, str]
    hits: dict[str, tuple[str, ...]] | None = None
    quests: tuple[Quest, ...] = ()


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
    fortunes = _fortunes(_read(directory, "fortunes.yaml"))
    raw = _read(directory, "achievements.yaml")
    missing = set(CODES) - set(raw or {})
    if missing:
        raise ValueError(f"achievements.yaml has no text for: {sorted(missing)}")
    achievements = {code: Achievement(**raw[code]) for code in CODES}
    names = _lines(_read(directory, "names.yaml"), "names.yaml")
    if len(set(names)) != len(names):
        raise ValueError("names.yaml must hold unique names")
    shoos = _read(directory, "shoos.yaml") or {}
    raw_hits = _read(directory, "hits.yaml")
    hits = {}
    for outcome in ("normal", "miss", "cap", "chain", "cap_knocked"):
        lines = _lines(raw_hits.get(outcome), f"hits.yaml {outcome}")
        if len(lines) < (4 if outcome == "cap_knocked" else 6):
            raise ValueError(f"hits.yaml has too few {outcome} reactions")
        hits[outcome] = lines
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
        hits=hits,
        quests=_quests(_read(directory, "quests.yaml")),
    )
