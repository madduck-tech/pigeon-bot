import pytest

from pigeon import __main__ as entry


def test_main_fails_without_token(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # keep a real .env in the repo out of this test
    monkeypatch.delenv("PIGEON_BOT_TOKEN", raising=False)
    monkeypatch.setenv("PIGEON_DB_PATH", str(tmp_path / "pigeon.db"))
    assert entry.main() == 1


def test_main_starts_bot_when_token_set(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PIGEON_BOT_TOKEN", "123:test")
    started = []

    async def fake_run_bot(settings) -> None:
        started.append(settings.bot_token.get_secret_value())

    monkeypatch.setattr(entry, "run_bot", fake_run_bot)
    assert entry.main() == 0
    assert started == ["123:test"]
