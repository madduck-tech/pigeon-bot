"""Entry point: `python -m pigeon`. Without a token it only reports the config problem."""

import asyncio
import logging
import sys

from pigeon.app import run_bot
from pigeon.config import Settings


def main() -> int:
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if settings.bot_token is None:
        logging.getLogger("pigeon").error("PIGEON_BOT_TOKEN is not set")
        return 1
    asyncio.run(run_bot(settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
