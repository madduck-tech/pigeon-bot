"""Stand scenarios. Each module has DESCRIPTION and `async def run(stand)`."""

from tools.stand.scenarios import chronicle, commands

SCENARIOS = {"commands": commands, "chronicle": chronicle}
