"""Tests for the Typer command-line interface."""

from pathlib import Path

from typer.testing import CliRunner

from maxicrawler import __version__
from maxicrawler.cli import app

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_init_and_config_commands(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / "maxicrawler.toml").exists()
    assert (tmp_path / "maxicrawler.db").exists()
    config_result = runner.invoke(app, ["config"])
    assert config_result.exit_code == 0
    assert "[maxicrawler]" in config_result.stdout
