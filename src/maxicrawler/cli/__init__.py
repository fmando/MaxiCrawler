"""Typer command-line interface for MaxiCrawler."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from maxicrawler import __version__
from maxicrawler.cli.inspection import (
    EXIT_UNDETERMINED,
    exit_code_for,
    render_inspection,
    render_json,
)
from maxicrawler.cli.summary import render_summary
from maxicrawler.config import DEFAULT_CONFIG_PATH, Settings
from maxicrawler.crawler import (
    DiscoveryPipeline,
    DiscoveryRepository,
    LocalDiscoveryService,
    NullDiscoveryRepository,
)
from maxicrawler.database import SQLiteDatabase, SQLiteDiscoveryRepository
from maxicrawler.domain import (
    Availability,
    ResourceInspection,
    ResourceRef,
    ScanSession,
    UrlClassification,
    UrlRecord,
)
from maxicrawler.events import EventBus
from maxicrawler.plugins import PluginResolver, create_default_registry
from maxicrawler.providers import (
    ProviderError,
    ProviderRegistry,
    RetryPolicy,
    UrllibTransport,
    create_default_provider_registry,
)
from maxicrawler.utils import configure_logging, normalize_url, strip_fragment

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
def info(
    url: Annotated[str, typer.Argument(help="Share link to describe.", show_default=False)],
    config_path: Annotated[
        Path, typer.Option("--config", help="TOML configuration file to use.")
    ] = DEFAULT_CONFIG_PATH,
    offline: Annotated[
        bool, typer.Option("--offline", help="Read the link only; contact no provider.")
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the result as a JSON document.")
    ] = False,
    max_entries: Annotated[
        int | None, typer.Option("--max-entries", help="How many folder entries to list.")
    ] = None,
) -> None:
    """Show what a share link points at, without downloading anything.

    This is the only command that contacts a provider; discover stays
    entirely offline. For a Mega link the provider asks for metadata without
    requesting a download URL, so no file content moves and no transfer quota
    is used. A decryption key in the link is used on this machine only and is
    never sent, printed, or stored.

    The exit code reports the verdict: 0 when the resource is available, 2 when
    the provider says it is gone, revoked, or blocked, and 3 when no statement
    could be obtained.
    """
    settings = Settings.from_toml(config_path)
    classification = _classify(url)
    registry = _build_provider_registry(settings, max_entries=max_entries)
    provider = registry.resolve(classification)
    if provider is None:
        msg = f"no provider can describe this link: {strip_fragment(url)}"
        raise typer.BadParameter(msg)
    try:
        ref = provider.reference(classification)
        inspection = _offline_inspection(ref) if offline else provider.inspect(ref)
    except ProviderError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(EXIT_UNDETERMINED) from error
    render = render_json if as_json else render_inspection
    typer.echo(render(inspection, provider.metadata))
    if not offline:
        raise typer.Exit(exit_code_for(inspection.availability))


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


def _classify(url: str) -> UrlClassification:
    """Return the plugin's verdict about *url*, without any network access."""
    try:
        record = UrlRecord(raw_url=url, normalized_url=normalize_url(url))
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    resolution = PluginResolver(create_default_registry()).resolve(record)
    if resolution.classification is None:
        msg = f"no plugin can classify this link: {strip_fragment(url)}"
        raise typer.BadParameter(msg)
    return resolution.classification


def _build_provider_registry(settings: Settings, *, max_entries: int | None) -> ProviderRegistry:
    """Return the providers, wired to the configured network behaviour."""
    transport = UrllibTransport(user_agent=settings.user_agent, timeout=settings.network_timeout)
    return create_default_provider_registry(
        transport=transport,
        retry=RetryPolicy(max_attempts=settings.network_retries),
        max_entries=max_entries if max_entries is not None else settings.max_entries,
    )


def _offline_inspection(ref: ResourceRef) -> ResourceInspection:
    """Return what the link alone states, with no request made."""
    return ResourceInspection(ref=ref, availability=Availability.UNKNOWN)


def main() -> None:
    """Run the command-line interface."""
    configure_logging()
    app()
