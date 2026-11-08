"""Stand scenarios. Each module has DESCRIPTION and `async def run(stand)`."""

from tools.stand.scenarios import commands, smoke

SCENARIOS = {"smoke": smoke, "commands": commands}
