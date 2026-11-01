import pytest

from pigeon.__main__ import main


def test_main_fails_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PIGEON_BOT_TOKEN", raising=False)
    monkeypatch.setenv("PIGEON_DB_PATH", "/tmp/pigeon-test.db")
    assert main() == 1


def test_main_succeeds_with_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIGEON_BOT_TOKEN", "123:test")
    assert main() == 0
