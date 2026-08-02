"""Tests for the Typer command-line interface."""

from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from maxicrawler import __version__
from maxicrawler.cli import app
from maxicrawler.cli.summary import render_summary
from maxicrawler.crawler import DiscoverySummary, PluginUsage
from maxicrawler.database import SQLiteDatabase, SQLiteDiscoveryRepository
from maxicrawler.domain import ScanSession, Statistics

runner = CliRunner()
DATA = Path(__file__).parent / "data" / "documents"


def make_summary(
    statistics: Statistics, plugin_usage: tuple[PluginUsage, ...] = ()
) -> DiscoverySummary:
    """Return a summary without running a discovery session."""
    return DiscoverySummary(
        session=ScanSession("session-1", datetime(2026, 8, 2, tzinfo=UTC)),
        statistics=statistics,
        plugin_usage=plugin_usage,
    )


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


def test_render_summary_matches_the_documented_layout() -> None:
    summary = make_summary(
        Statistics(documents_processed=12, discovered_urls=221, duplicate_urls=26),
        (PluginUsage("generic", 221),),
    )

    assert render_summary(summary) == (
        "Documents processed: 12\n"
        "URLs discovered: 247\n"
        "Unique URLs: 221\n"
        "Duplicates removed: 26\n"
        "\n"
        "Plugin usage:\n"
        "generic: 221"
    )


def test_render_summary_reports_unresolved_urls_only_when_present() -> None:
    without = render_summary(make_summary(Statistics(discovered_urls=1)))
    with_unresolved = render_summary(make_summary(Statistics(discovered_urls=1, unresolved_urls=1)))

    assert "Unresolved URLs" not in without
    assert "Unresolved URLs: 1" in with_unresolved


def test_render_summary_reports_missing_plugin_usage() -> None:
    assert render_summary(make_summary(Statistics())).endswith("Plugin usage:\nnone")


def test_discover_command_reports_a_summary(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]

    result = runner.invoke(app, ["discover", str(DATA)])

    assert result.exit_code == 0
    assert "Documents processed: 4" in result.stdout
    assert "URLs discovered: 22" in result.stdout
    assert "Unique URLs: 21" in result.stdout
    assert "Duplicates removed: 1" in result.stdout
    assert "generic: 21" in result.stdout


def test_discover_command_reports_each_plugin_separately(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]
    mega = Path(__file__).parent / "data" / "mega"

    result = runner.invoke(app, ["discover", str(mega), "--no-persist"])

    assert result.exit_code == 0
    assert "Documents processed: 2" in result.stdout
    assert "Duplicates removed: 1" in result.stdout
    assert "mega: 13" in result.stdout
    assert "generic: 6" in result.stdout


def test_discover_command_accepts_a_single_file(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]

    result = runner.invoke(app, ["discover", str(DATA / "release-notes.txt")])

    assert result.exit_code == 0
    assert "Documents processed: 1" in result.stdout
    assert "Unique URLs: 5" in result.stdout


def test_discover_command_persists_by_default(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]

    result = runner.invoke(app, ["discover", str(DATA)])

    assert result.exit_code == 0
    database = tmp_path / "maxicrawler.db"
    assert database.exists()
    with SQLiteDatabase(database).connect() as connection:
        stored = connection.execute("SELECT COUNT(*) AS total FROM discovered_urls").fetchone()
    assert stored["total"] == 21


def test_discover_command_can_skip_persistence(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]

    result = runner.invoke(app, ["discover", str(DATA), "--no-persist"])

    assert result.exit_code == 0
    assert not (tmp_path / "maxicrawler.db").exists()


def test_discover_command_honours_the_configured_database(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]
    config = tmp_path / "custom.toml"
    config.write_text('[maxicrawler]\ndatabase_path = "custom.db"\n', encoding="utf-8")

    result = runner.invoke(app, ["discover", str(DATA), "--config", str(config)])

    assert result.exit_code == 0
    repository = SQLiteDiscoveryRepository(SQLiteDatabase(tmp_path / "custom.db"))
    assert (tmp_path / "custom.db").exists()
    assert repository.database.path.name == "custom.db"


def test_discover_command_rejects_a_missing_path(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]

    result = runner.invoke(app, ["discover", str(tmp_path / "missing")])

    assert result.exit_code != 0


def test_discover_command_handles_a_directory_without_documents(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]
    empty = tmp_path / "empty"
    empty.mkdir()

    result = runner.invoke(app, ["discover", str(empty), "--no-persist"])

    assert result.exit_code == 0
    assert "Documents processed: 0" in result.stdout
    assert "Plugin usage:\nnone" in result.stdout
