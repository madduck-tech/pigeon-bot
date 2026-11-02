"""An rng for tests about what happens on a flying day, not about which days are picked."""

import random
from pathlib import Path

from pigeon.content import load_content

CONTENT = load_content(Path(__file__).resolve().parents[1] / "content")


class EveryDay(random.Random):
    """Picks every day. Times inside the day stay random, as in production."""

    def random(self) -> float:
        return 0.0

    def getrandbits(self, k: int) -> int:
        return super().getrandbits(k)
