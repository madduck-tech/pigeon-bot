"""Entry point: `python -m pigeon`. M1 skeleton: checks config, bot loop not written yet."""

import logging
import sys

from pigeon import __version__
from pigeon.config import Settings


def main() -> int:
    settings = Settings()
    logging.basicConfig(level=settings.log_level.upper())
    log = logging.getLogger("pigeon")
    if settings.bot_token is None:
        log.error("PIGEON_BOT_TOKEN is not set")
        return 1
    log.info("pigeon %s: config OK, bot loop not implemented yet (M1 skeleton)", __version__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
