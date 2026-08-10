"""Typer command-line interface for MaxiCrawler."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from maxicrawler import __version__
from maxicrawler.api.errors import MISSING_EXTRA, WebDependencyError
from maxicrawler.app import CrawlService, DownloadService
from maxicrawler.cli.crawling import (
    EXIT_FETCH_FAILED,
    EXIT_NOT_A_PAGE,
    render_crawl,
    render_crawl_json,
)
from maxicrawler.cli.crawling import (
    exit_code_for as crawl_exit_code_for,
)
from maxicrawler.cli.downloads import (
    exit_code_for as download_exit_code_for,
)
from maxicrawler.cli.downloads import (
    render_plan,
    render_report,
)
from maxicrawler.cli.inspection import (
    EXIT_UNDETERMINED,
    exit_code_for,
    render_inspection,
    render_json,
)
from maxicrawler.cli.serving import (
    EXIT_WEB_UNAVAILABLE,
    banner,
    exposure_notice,
    is_loopback,
    refusal,
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
from maxicrawler.downloader import (
    NullProgressReporter,
    ProgressReporter,
    RichProgressReporter,
    SourceError,
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
from maxicrawler.web import ContentTypeError, FetchError, PolicyRefusedError

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
def crawl(
    url: Annotated[str, typer.Argument(help="Web page to start from.", show_default=False)],
    depth: Annotated[
        int | None,
        typer.Option("--depth", "-d", help="How many links deep to follow. 0 is the seed alone."),
    ] = None,
    same_domain: Annotated[
        bool | None,
        typer.Option(
            "--same-domain/--any-domain",
            help="Stay on the seed's host, or follow links anywhere.",
        ),
    ] = None,
    include_subdomains: Annotated[
        bool,
        typer.Option("--include-subdomains", help="Count subdomains as the same domain."),
    ] = False,
    max_pages: Annotated[
        int | None, typer.Option("--max-pages", help="Stop after this many pages.")
    ] = None,
    respect_robots: Annotated[
        bool | None,
        typer.Option(
            "--respect-robots/--ignore-robots",
            help="Obey each host's robots.txt, or crawl regardless of it.",
        ),
    ] = None,
    delay: Annotated[
        float | None,
        typer.Option("--delay", help="Seconds to leave between requests to one host."),
    ] = None,
    allow_private: Annotated[
        bool,
        typer.Option(
            "--allow-private",
            help="Permit loopback and private addresses, such as your own intranet.",
        ),
    ] = False,
    config_path: Annotated[
        Path, typer.Option("--config", help="TOML configuration file to use.")
    ] = DEFAULT_CONFIG_PATH,
    persist: Annotated[
        bool, typer.Option("--persist/--no-persist", help="Store results in the database.")
    ] = True,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the result as a JSON document.")
    ] = False,
    prose: Annotated[
        bool,
        typer.Option("--prose/--no-prose", help="Also read URLs written as plain text."),
    ] = True,
) -> None:
    """Crawl a website, following links from the page you name.

    By default exactly one page is fetched and its links are reported without
    being followed. --depth 2 follows them two levels, --depth 3 three, and so
    on. Whatever is found goes through the same discovery pipeline and the same
    plugins as the URLs discover finds in a local document.

    Links are followed onto other hosts unless --same-domain says otherwise,
    because finding a share link on Mega or Pixeldrain is as much the point as
    walking one site. --max-pages is the ceiling that keeps that finite.

    Nothing is downloaded and no provider is contacted. Only HTML is read:
    JavaScript is not executed, no cookie is stored and no form is submitted,
    so a page that builds its links in the browser will appear to have fewer of
    them than it shows a reader.

    Each host's robots.txt is obeyed, and a URL it forbids is reported as
    skipped rather than fetched. --ignore-robots turns that off for one run,
    which is a decision to make deliberately: what you point this at is your
    responsibility either way.

    A host asking for a Crawl-delay is waited for. Nothing else is: --delay
    adds a wait between requests to one host when you want to be gentler than
    anybody asked.

    Addresses inside this machine or this network are refused, because a
    crawler is a thing that fetches URLs somebody else may have written.
    --allow-private lifts that for crawling your own intranet; a cloud metadata
    service stays refused regardless.

    The exit code is 0 when the crawl ran to an end or to a limit it was given,
    5 when the starting page could not be retrieved or was refused, 6 when it
    was not a page, and 7 when the crawl was interrupted.
    """
    settings = Settings.from_toml(config_path)
    if delay is not None:
        settings = replace(settings, crawl_delay=delay)
    if allow_private:
        settings = replace(settings, allow_private_networks=True)
    service = CrawlService(settings)
    try:
        session = service.build_session(
            url,
            depth=depth,
            max_pages=max_pages,
            same_domain=same_domain,
            include_subdomains=include_subdomains,
            respect_robots=respect_robots,
            scan_prose=prose,
        )
    except ValueError as error:
        raise typer.BadParameter(f"{error}: {url}") from error
    try:
        report = service.run(session, persist=persist)
    except ContentTypeError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(EXIT_NOT_A_PAGE) from error
    except (FetchError, PolicyRefusedError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(EXIT_FETCH_FAILED) from error
    render = render_crawl_json if as_json else render_crawl
    typer.echo(render(report))
    raise typer.Exit(crawl_exit_code_for(report))


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
def download(
    source: Annotated[
        str,
        typer.Argument(
            help="A share link, a document holding links, or a directory of documents.",
            show_default=False,
        ),
    ],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Library directory to store into."),
    ] = None,
    config_path: Annotated[
        Path, typer.Option("--config", help="TOML configuration file to use.")
    ] = DEFAULT_CONFIG_PATH,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report what would be downloaded; transfer nothing.")
    ] = False,
    progress: Annotated[
        bool, typer.Option("--progress/--no-progress", help="Draw progress bars on stderr.")
    ] = True,
    max_entries: Annotated[
        int | None, typer.Option("--max-entries", help="How many folder entries to consider.")
    ] = None,
) -> None:
    """Download what a source points at into the library.

    The source may be a share link, a text, Markdown, or HTML document holding
    links, or a directory of such documents; there is one command because from
    the outside they all answer the same question. Documents are read with the
    same rules as discover, so whatever discover finds is what download fetches.

    Downloads land in the configured library, one directory per resource, each
    with a metadata.json describing where it came from. A resource the library
    already holds is skipped without contacting the provider, and nothing is
    ever overwritten automatically. The web interface stores into the same
    library through the same service, so both clients see one set of files.

    The exit code is 0 when everything the source asked for is in the library,
    and 4 when something was not: a failed transfer, a revoked share, or a link
    no provider handles. The report says which.
    """
    service = DownloadService(Settings.from_toml(config_path))
    manager = service.build_manager(
        output=output,
        max_entries=max_entries,
        reporter=_build_reporter(progress=progress and not dry_run),
    )
    try:
        plan = manager.plan(source)
    except SourceError as error:
        raise typer.BadParameter(str(error)) from error
    if dry_run:
        typer.echo(render_plan(plan, manager.library.root))
        raise typer.Exit(0 if not plan.unresolved else 4)
    report = manager.run(plan)
    typer.echo(render_report(report))
    raise typer.Exit(download_exit_code_for(report))


@app.command()
def serve(
    host: Annotated[
        str,
        typer.Option(
            help="Address to listen on. Anything but a loopback address needs --allow-remote."
        ),
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
    config_path: Annotated[
        Path, typer.Option("--config", help="TOML configuration file to use.")
    ] = DEFAULT_CONFIG_PATH,
    allow_remote: Annotated[
        bool,
        typer.Option("--allow-remote", help="Permit binding an address others can reach."),
    ] = False,
) -> None:
    """Run the web interface.

    The same crawls the crawl command runs, through the same service, in a
    browser. Nothing here can do anything the command line cannot; it is a
    second client, not a second program.

    It listens on 127.0.0.1 by default, where only this machine can reach it.
    The interface has no authentication and can start crawls, so listening
    anywhere else needs --allow-remote and says what that means.

    Runs until interrupted. Crawls still running when it stops are asked to
    stop, and finish the page they are on before they do.

    The exit code is 8 when the optional web extra is not installed.
    """
    if not is_loopback(host) and not allow_remote:
        raise typer.BadParameter(refusal(host), param_hint="--host")
    try:
        import uvicorn

        from maxicrawler.api import create_app
    except (ImportError, WebDependencyError) as error:
        # uvicorn and starlette arrive together in the web extra, so one
        # message covers both. It names the command that installs them.
        typer.echo(MISSING_EXTRA, err=True)
        raise typer.Exit(EXIT_WEB_UNAVAILABLE) from error
    application = create_app(settings=Settings.from_toml(config_path), config_path=config_path)
    if not is_loopback(host):
        typer.echo(exposure_notice(host, port), err=True)
    typer.echo(banner(host, port))
    uvicorn.run(application, host=host, port=port, log_level=_uvicorn_log_level())


def _uvicorn_log_level() -> str:
    """Return how loud uvicorn should be.

    Quiet, because it would otherwise print a line per request and bury the
    one line that says where the interface is. Anything that matters about a
    crawl is on the page it belongs to.
    """
    return "warning"


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
    """Return the providers ``info`` may ask, wired to the configured network.

    Composed without a stream transport, so this registry has no way to move
    content at all. That is what keeps ``info`` unable to download by
    construction rather than by convention — and the providers say so through
    their capabilities rather than by failing when asked.

    The registry that *can* transfer is built by
    :class:`~maxicrawler.app.DownloadService`, which is the only place that
    should be composing one.
    """
    return create_default_provider_registry(
        transport=UrllibTransport(user_agent=settings.user_agent, timeout=settings.network_timeout),
        retry=RetryPolicy(max_attempts=settings.network_retries),
        max_entries=max_entries if max_entries is not None else settings.max_entries,
    )


def _build_reporter(*, progress: bool) -> ProgressReporter:
    """Return the progress reporter a run should use."""
    return RichProgressReporter() if progress else NullProgressReporter()


def _offline_inspection(ref: ResourceRef) -> ResourceInspection:
    """Return what the link alone states, with no request made."""
    return ResourceInspection(ref=ref, availability=Availability.UNKNOWN)


def main() -> None:
    """Run the command-line interface."""
    configure_logging()
    app()
