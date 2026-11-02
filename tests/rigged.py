"""An rng for tests about what happens on a flying day, not about which days are picked."""

import random


class EveryDay(random.Random):
    """Picks every day. Times inside the day stay random, as in production."""

    def random(self) -> float:
        return 0.0

    def getrandbits(self, k: int) -> int:
        return super().getrandbits(k)
