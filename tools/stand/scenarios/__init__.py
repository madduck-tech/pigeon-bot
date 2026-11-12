"""Stand scenarios. Each module has DESCRIPTION and `async def run(stand)`."""

from tools.stand.scenarios import chronicle, commands, forbidden

SCENARIOS = {"commands": commands, "chronicle": chronicle, "forbidden": forbidden}
