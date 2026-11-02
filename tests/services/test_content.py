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
    (tmp_path / "fortunes.yaml").write_text("- a\n", encoding="utf-8")
    (tmp_path / "achievements.yaml").write_text(
        "sniper:\n  name: x\n  description: y\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="bullseye"):
        load_content(tmp_path)


def test_empty_fortunes_fail_at_startup(tmp_path) -> None:
    (tmp_path / "fortunes.yaml").write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="fortunes"):
        load_content(tmp_path)


def test_repository_has_names_for_years_of_votes() -> None:
    assert len(load_content(CONTENT).names) >= 36


def test_repeated_name_fails_at_startup(tmp_path) -> None:
    for name in ("fortunes", "achievements"):
        (tmp_path / f"{name}.yaml").write_text(
            (CONTENT / f"{name}.yaml").read_text("utf-8"), "utf-8"
        )
    (tmp_path / "names.yaml").write_text("- Гена\n- Гена\n", encoding="utf-8")
    with pytest.raises(ValueError, match="names"):
        load_content(tmp_path)
