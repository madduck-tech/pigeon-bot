"""Stand scenarios. Each module has DESCRIPTION and `async def run(stand)`."""

from tools.stand.scenarios import arrivals, chronicle, commands, courier, forbidden

SCENARIOS = {
    "arrivals": arrivals,
    "commands": commands,
    "chronicle": chronicle,
    "courier": courier,
    "forbidden": forbidden,
}
