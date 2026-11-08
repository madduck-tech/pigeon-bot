"""Stand scenarios. Each module has DESCRIPTION and `async def run(stand)`."""

from tools.stand.scenarios import chronicle, commands, smoke

SCENARIOS = {"smoke": smoke, "commands": commands, "chronicle": chronicle}
