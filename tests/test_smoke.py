from pigeon import __version__
from pigeon.config import Settings


def test_package_imports_and_has_version() -> None:
    assert __version__ == "0.1.0"


def test_settings_defaults(monkeypatch) -> None:
    monkeypatch.delenv("PIGEON_BOT_TOKEN", raising=False)
    monkeypatch.delenv("PIGEON_DB_PATH", raising=False)
    settings = Settings(_env_file=None)
    assert settings.bot_token is None
    assert str(settings.db_path) == "data/pigeon.db"
    assert settings.log_level == "INFO"
