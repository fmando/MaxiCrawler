"""Typer command-line interface for MaxiCrawler."""

from pathlib import Path
from typing import Annotated

import typer

from maxicrawler import __version__
from maxicrawler.config import DEFAULT_CONFIG_PATH, Settings
from maxicrawler.database import SQLiteDatabase
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
def version() -> None:
    """Print the installed MaxiCrawler version."""
    typer.echo(__version__)


def main() -> None:
    """Run the command-line interface."""
    configure_logging()
    app()
