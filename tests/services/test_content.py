from pathlib import Path

import pytest

from pigeon.content import load_content
from pigeon.services import achievements

CONTENT = Path(__file__).resolve().parents[2] / "content"


def test_repository_content_loads() -> None:
    content = load_content(CONTENT)
    assert len(content.fortunes) >= 30
    assert set(content.achievements) == set(achievements.CODES)


def test_achievement_without_text_fails_at_startup(tmp_path) -> None:
    (tmp_path / "fortunes.yaml").write_text(
        (CONTENT / "fortunes.yaml").read_text(), encoding="utf-8"
    )
    (tmp_path / "achievements.yaml").write_text(
        "sniper:\n  name: x\n  description: y\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="early_bird"):
        load_content(tmp_path)


def test_empty_fortunes_fail_at_startup(tmp_path) -> None:
    (tmp_path / "fortunes.yaml").write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="fortunes"):
        load_content(tmp_path)


def test_repository_has_enough_pigeons_and_phrases() -> None:
    content = load_content(CONTENT)
    assert len(content.names) >= 36
    assert len(content.arrivals) >= 10
    assert content.shoos.actions and content.shoos.stayed and content.shoos.flew


def test_repeated_name_fails_at_startup(tmp_path) -> None:
    _copy_content(tmp_path)
    (tmp_path / "names.yaml").write_text("- Гена\n- Гена\n", encoding="utf-8")
    with pytest.raises(ValueError, match="names"):
        load_content(tmp_path)


def test_arrival_phrase_without_a_name_fails_at_startup(tmp_path) -> None:
    _copy_content(tmp_path)
    (tmp_path / "arrivals.yaml").write_text("- Курлык.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="arrivals"):
        load_content(tmp_path)


def test_empty_shoo_reactions_fail_at_startup(tmp_path) -> None:
    _copy_content(tmp_path)
    (tmp_path / "shoos.yaml").write_text("actions: [a]\nstayed: [b]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="flew"):
        load_content(tmp_path)


def _copy_content(target) -> None:
    for path in CONTENT.glob("*.yaml"):
        (target / path.name).write_text(path.read_text("utf-8"), "utf-8")


def test_every_offered_timezone_is_real() -> None:
    timezones = load_content(CONTENT).timezones
    assert timezones["Москва"] == "Europe/Moscow"
    assert len(set(timezones.values())) == len(timezones)


def test_wrong_timezone_fails_at_startup(tmp_path) -> None:
    _copy_content(tmp_path)
    (tmp_path / "timezones.yaml").write_text("Москва: Europe/Mars\n", encoding="utf-8")
    with pytest.raises(Exception, match="Mars"):
        load_content(tmp_path)
