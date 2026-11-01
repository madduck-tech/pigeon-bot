"""Process-level settings read from environment variables (prefix PIGEON_).

Per-chat settings (timezone, quiet hours, pause) live in the database, not here.
"""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PIGEON_", env_file=".env", extra="ignore")

    bot_token: SecretStr | None = None
    db_path: Path = Path("./data/pigeon.db")
    log_level: str = "INFO"
