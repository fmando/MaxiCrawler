"""Typer command-line interface for MaxiCrawler."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from maxicrawler import __version__
from maxicrawler.cli.summary import render_summary
from maxicrawler.config import DEFAULT_CONFIG_PATH, Settings
from maxicrawler.crawler import (
    DiscoveryPipeline,
    DiscoveryRepository,
    LocalDiscoveryService,
    NullDiscoveryRepository,
)
from maxicrawler.database import SQLiteDatabase, SQLiteDiscoveryRepository
from maxicrawler.domain import ScanSession
from maxicrawler.events import EventBus
from maxicrawler.utils import configure_logging

app = typer.Typer(help="Configuration and runtime tools for MaxiCrawler.", no_args_is_help=True)
ConfigPath = Annotated[Path, typer.Option(help="TOML configuration file to use.")]


@app.command()
def init(path: ConfigPath = DEFAULT_CONFIG_PATH) -> None:
    """Create the default TOML configuration and initialize SQLite storage."""
    if path.exists():
        raise typer.BadParameter(f"configuration already exists: {path}")
    settings = Settings()
    path.write_text(settings.to_toml(), encoding="utf-8")
    SQLiteDatabase(settings.database_path).initialize()
    typer.echo(f"Created {path}")


@app.command()
def config(path: ConfigPath = DEFAULT_CONFIG_PATH) -> None:
    """Print the effective TOML configuration."""
    typer.echo(Settings.from_toml(path).to_toml(), nl=False)


@app.command()
def discover(
    source: Annotated[
        Path, typer.Argument(help="File or directory to scan for URLs.", show_default=False)
    ],
    config_path: Annotated[
        Path, typer.Option("--config", help="TOML configuration file to use.")
    ] = DEFAULT_CONFIG_PATH,
    persist: Annotated[
        bool, typer.Option("--persist/--no-persist", help="Store results in the database.")
    ] = True,
) -> None:
    """Discover URLs in local documents; no network access is performed.

    Supported formats are plain text, Markdown, and HTML. A directory is
    processed recursively and unsupported files are skipped.
    """
    if not source.exists():
        raise typer.BadParameter(f"path does not exist: {source}")
    settings = Settings.from_toml(config_path)
    repository = _build_repository(settings, persist=persist)
    service = LocalDiscoveryService(DiscoveryPipeline(EventBus()), repository=repository)
    session = ScanSession(session_id=uuid4().hex, started_at=datetime.now(UTC))
    summary = service.run(source, session)
    typer.echo(render_summary(summary))


@app.command()
def version() -> None:
    """Print the installed MaxiCrawler version."""
    typer.echo(__version__)


def _build_repository(settings: Settings, *, persist: bool) -> DiscoveryRepository:
    """Return the repository the discovery run should write to."""
    if not persist:
        return NullDiscoveryRepository()
    repository = SQLiteDiscoveryRepository(SQLiteDatabase(settings.database_path))
    repository.initialize()
    return repository


def main() -> None:
    """Run the command-line interface."""
    configure_logging()
    app()
