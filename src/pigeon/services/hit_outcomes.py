"""Roll and preserve the result of one attempt."""

import random
from dataclasses import dataclass
from enum import StrEnum


class Outcome(StrEnum):
    NORMAL = "normal"
    MISS = "miss"
    CAP = "cap"
    CHAIN = "chain"
    CAP_KNOCKED = "cap_knocked"


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    points: int
    reaction: str


def roll(
    rng: random.Random, lines: dict[str, tuple[str, ...]], pigeon_type: str, cap_knocked: bool
) -> Result:
    if pigeon_type == "capped" and not cap_knocked:
        outcome, points = Outcome.CAP_KNOCKED, 0
    else:
        value = rng.random()
        if value < 0.65:
            outcome, points = Outcome.NORMAL, 1
        elif value < 0.85:
            outcome, points = Outcome.MISS, 0
        elif value < 0.95:
            outcome, points = Outcome.CAP, 2
        else:
            outcome, points = Outcome.CHAIN, 3
    if pigeon_type == "golden":
        points *= 2
    return Result(outcome, points, rng.choice(lines[outcome]))
