"""Process-level settings read from environment variables (prefix PIGEON_).

Per-chat settings (timezone, quiet hours, pause) live in the database, not here.
"""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from pigeon.llm.opencode import DEFAULT_BASE_URL, DEFAULT_MODEL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PIGEON_", env_file=".env", extra="ignore")

    bot_token: SecretStr | None = None
    db_path: Path = Path("./data/pigeon.db")
    content_dir: Path = Path("./content")
    log_level: str = "INFO"
    # Weekly chronicle (ADR-0014): without a key it is off and nothing leaves the bot.
    chronicle_api_key: SecretStr | None = None
    chronicle_model: str = DEFAULT_MODEL
    chronicle_base_url: str = DEFAULT_BASE_URL
