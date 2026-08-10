"""The HTTP application, and the one place it is assembled.

Starlette rather than FastAPI, deliberately. FastAPI's value is request-model
validation and a generated OpenAPI document; this serves server-rendered HTML
with three form fields, so neither would be earning its keep — and Starlette is
the layer FastAPI is built on, one dependency lighter. If a JSON API for other
tools arrives later, that is when FastAPI is worth its weight.

Importing this module without the optional ``web`` extra raises
:class:`~maxicrawler.api.errors.WebDependencyError`, whose message names the
install command. :mod:`maxicrawler.api` itself stays importable either way, so
nothing has to guess before asking.

The application holds a :class:`~maxicrawler.app.CrawlService` and a
:class:`~maxicrawler.app.DownloadService` and nothing else. It never reaches for
a provider, a downloader or the library itself — a rule you can check by
reading, and that ``tests/test_api_boundaries.py`` checks by parsing.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from maxicrawler.api.errors import MISSING_EXTRA, WebDependencyError
from maxicrawler.app import CrawlService, DownloadService, LibraryService
from maxicrawler.config import DEFAULT_CONFIG_PATH, Settings

try:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles
except ImportError as error:  # pragma: no cover - depends on the environment
    raise WebDependencyError(MISSING_EXTRA) from error

from maxicrawler.api import routes  # noqa: E402 - only importable behind the guard
from maxicrawler.api.downloads import DownloadRuns  # noqa: E402
from maxicrawler.api.jobs import CrawlJobs  # noqa: E402


def create_app(
    *,
    service: CrawlService | None = None,
    settings: Settings | None = None,
    jobs: CrawlJobs | None = None,
    downloads: DownloadRuns | None = None,
    library: LibraryService | None = None,
    config_path: Path | None = None,
) -> Starlette:
    """Return the MaxiCrawler web application.

    Every collaborator is injectable so a test can drive the routes without a
    socket, a database or a worker pool of its own. Given none of them, the
    application reads the configuration from its default location, exactly as
    the CLI does — which it now actually does rather than merely saying so.

    *config_path* is remembered so the settings page can name where the values
    came from. It is only a label: a caller that has already built its own
    settings is the authority on those, and passing the path it read them from
    is how the page can say which file to edit.
    """
    source: Path | None = config_path
    if service is None and settings is None:
        source = config_path if config_path is not None else DEFAULT_CONFIG_PATH
        settings = Settings.from_toml(source)
    effective = settings if settings is not None else Settings()
    crawl_service = service if service is not None else CrawlService(effective)
    registry = jobs if jobs is not None else CrawlJobs(crawl_service)
    transfers = (
        downloads
        if downloads is not None
        else DownloadRuns(DownloadService(crawl_service.settings))
    )
    shelf = library if library is not None else LibraryService(crawl_service.settings)

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        """Ask everything running in the background to stop when the server does.

        Without waiting: a crawl asked to stop finishes the page it is on, and a
        shutdown that blocked on a slow fetch would look like a hang. A transfer
        already moving is not interrupted at all — there is no cooperative stop
        yet — but an abandoned one leaves no half file in the library, because
        content becomes visible only once it is whole.
        """
        try:
            yield
        finally:
            registry.shutdown(wait=False)
            transfers.shutdown(wait=False)

    application = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/", routes.dashboard, methods=["GET"], name="dashboard"),
            Route("/crawls", routes.crawls, methods=["GET"], name="crawls"),
            Route("/crawls", routes.start_crawl, methods=["POST"], name="start_crawl"),
            # Before the page, because `{job_id}` would otherwise swallow the
            # suffix and answer a request for JSON with HTML.
            Route("/crawls/{job_id}.json", routes.crawl_json, methods=["GET"], name="crawl_json"),
            Route("/crawls/{job_id}", routes.crawl_detail, methods=["GET"], name="crawl_detail"),
            Route(
                "/crawls/{job_id}/events",
                routes.crawl_events,
                methods=["GET"],
                name="crawl_events",
            ),
            Route("/crawls/{job_id}/stop", routes.stop_crawl, methods=["POST"], name="stop_crawl"),
            Route("/downloads", routes.start_download, methods=["POST"], name="start_download"),
            Route(
                "/downloads/{download_id}",
                routes.download_detail,
                methods=["GET"],
                name="download_detail",
            ),
            Route(
                "/downloads/{download_id}/events",
                routes.download_events,
                methods=["GET"],
                name="download_events",
            ),
            Route("/library", routes.library, methods=["GET"], name="library"),
            Route("/settings", routes.settings, methods=["GET"], name="settings"),
            Route("/health", health, methods=["GET"], name="health"),
            Mount(
                "/static",
                app=StaticFiles(directory=str(routes.STATIC_DIRECTORY)),
                name="static",
            ),
        ],
    )
    application.state.crawl_service = crawl_service
    application.state.jobs = registry
    application.state.downloads = transfers
    application.state.library = shelf
    application.state.config_path = source
    return application


async def health(request: Request) -> JSONResponse:
    """Report that the server is answering.

    Deliberately the first route. It is what proves the event loop is still
    free while a crawl is running on a worker thread, which is the property the
    whole background-job design exists to preserve.
    """
    return JSONResponse({"status": "ok"})
