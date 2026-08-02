"""Tests for TOML-backed settings."""

from pathlib import Path

import pytest

from maxicrawler.config import Settings


def test_settings_load_from_toml(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(
        (
            '[maxicrawler]\nuser_agent = "TestBot"\n'
            'database_path = "data/app.db"\nlog_level = "debug"\n'
        ),
        encoding="utf-8",
    )

    settings = Settings.from_toml(path)

    assert settings.user_agent == "TestBot"
    assert settings.database_path == Path("data/app.db")
    assert settings.log_level == "DEBUG"


def test_settings_reject_non_string_toml_values(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text('[maxicrawler]\nuser_agent = "TestBot"\nlog_level = 5\n', encoding="utf-8")

    with pytest.raises(ValueError, match="log_level must be a string"):
        Settings.from_toml(path)
