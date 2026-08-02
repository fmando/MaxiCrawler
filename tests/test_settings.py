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


def test_network_settings_have_conservative_defaults() -> None:
    settings = Settings()

    assert settings.network_timeout == 30.0
    assert settings.network_retries == 3
    assert settings.max_entries == 1000


def test_network_settings_load_from_toml(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(
        "[maxicrawler]\nnetwork_timeout = 2.5\nnetwork_retries = 1\nmax_entries = 50\n",
        encoding="utf-8",
    )

    settings = Settings.from_toml(path)

    assert settings.network_timeout == 2.5
    assert settings.network_retries == 1
    assert settings.max_entries == 50


def test_an_integer_timeout_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text("[maxicrawler]\nnetwork_timeout = 5\n", encoding="utf-8")

    assert Settings.from_toml(path).network_timeout == 5.0


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ('[maxicrawler]\nnetwork_timeout = "soon"\n', "network_timeout must be a number"),
        ("[maxicrawler]\nnetwork_retries = 1.5\n", "network_retries must be an integer"),
        ("[maxicrawler]\nmax_entries = true\n", "max_entries must be an integer"),
    ],
)
def test_network_settings_reject_the_wrong_type(
    tmp_path: Path, document: str, message: str
) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        Settings.from_toml(path)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"network_timeout": 0.0}, "network_timeout must be positive"),
        ({"network_retries": 0}, "network_retries must be at least 1"),
        ({"max_entries": 0}, "max_entries must be at least 1"),
    ],
)
def test_network_settings_reject_impossible_values(kwargs: dict[str, float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_network_settings_round_trip_through_toml(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    original = Settings(network_timeout=7.5, network_retries=2, max_entries=25)
    path.write_text(original.to_toml(), encoding="utf-8")

    assert Settings.from_toml(path) == original


def test_the_library_path_has_a_documented_default() -> None:
    assert Settings().library_path == Path("library")


def test_the_library_path_loads_from_toml(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text('[maxicrawler]\nlibrary_path = "archive/downloads"\n', encoding="utf-8")

    assert Settings.from_toml(path).library_path == Path("archive/downloads")


def test_an_empty_library_path_is_rejected() -> None:
    with pytest.raises(ValueError, match="library_path must not be empty"):
        Settings(library_path=Path(" "))


def test_the_library_path_round_trips_through_toml(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    original = Settings(library_path=Path("archive/downloads"))
    path.write_text(original.to_toml(), encoding="utf-8")

    assert Settings.from_toml(path) == original
