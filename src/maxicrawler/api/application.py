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
from maxicrawler.app import (
    CrawlService,
    DiscoveryService,
    DownloadService,
    LibraryService,
    LinkState,
)
from maxicrawler.config import DEFAULT_CONFIG_PATH, Settings

try:
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles
except ImportError as error:  # pragma: no cover - depends on the environment
    raise WebDependencyError(MISSING_EXTRA) from error

from maxicrawler.api import routes  # noqa: E402 - only importable behind the guard
from maxicrawler.api.downloads import TransferQueue  # noqa: E402
from maxicrawler.api.jobs import CrawlJobs  # noqa: E402
from maxicrawler.api.origin import SameOriginMiddleware  # noqa: E402


def create_app(
    *,
    service: CrawlService | None = None,
    settings: Settings | None = None,
    jobs: CrawlJobs | None = None,
    downloads: TransferQueue | None = None,
    library: LibraryService | None = None,
    discovery: DiscoveryService | None = None,
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
        else TransferQueue(DownloadService(crawl_service.settings))
    )
    # The queue is handed to the library the same way the library is handed to
    # the report below: as one bound method answering one question in bulk.
    # Neither service learns what the other is, and the command line — which has
    # no queue to speak of — builds the same service without it.
    shelf = (
        library
        if library is not None
        else LibraryService(crawl_service.settings, queued=transfers.pending)
    )
    # The download service answers "could this be fetched?" from a provider
    # registry it builds once and caches. Handing that same instance over rather
    # than a second one is why the resolver is injected: two registries would be
    # two sets of providers to keep in step, for one identical answer.
    #
    # The states are wired the same way, and this is the only place that knows
    # which collaborator answers which. `DiscoveryService` is handed a mapping of
    # bound methods and never learns that one of them is a library and the other
    # a queue; a state added later is a member, a resolver and a label.
    findings = (
        discovery
        if discovery is not None
        else DiscoveryService(
            crawl_service.settings,
            downloadable=transfers.service.downloadable,
            states={
                LinkState.IN_LIBRARY: shelf.stored,
                LinkState.IN_QUEUE: transfers.pending,
            },
        )
    )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        """Ask everything running in the background to stop when the server does.

        Without waiting: a crawl asked to stop finishes the page it is on, and a
        shutdown that blocked on a slow fetch would look like a hang. A transfer
        stops at its next chunk, which is as close to immediate as a cooperative
        stop gets — and leaves no half file either way, because content becomes
        visible only once it is whole.
        """
        try:
            yield
        finally:
            registry.shutdown(wait=False)
            transfers.shutdown(wait=False)

    application = Starlette(
        lifespan=lifespan,
        # The only middleware, and it guards every unsafe method at once rather
        # than every route remembering to. See `maxicrawler.api.origin` for what
        # it decides and, more importantly, what it is not.
        middleware=[Middleware(SameOriginMiddleware)],
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
            # Under the crawl rather than under /downloads: what it queues is
            # decided by re-running that crawl's link query on the server, so
            # the crawl is what it is addressed against.
            Route(
                "/crawls/{job_id}/downloads",
                routes.queue_matches,
                methods=["POST"],
                name="queue_matches",
            ),
            Route("/downloads", routes.downloads, methods=["GET"], name="downloads"),
            Route("/downloads", routes.start_download, methods=["POST"], name="start_download"),
            Route(
                "/downloads/selection",
                routes.queue_selection,
                methods=["POST"],
                name="queue_selection",
            ),
            # Before the page, for the same reason `{job_id}.json` is: a path
            # parameter matches any single segment, and "pause" is one.
            Route(
                "/downloads/pause",
                routes.pause_downloads,
                methods=["POST"],
                name="pause_downloads",
            ),
            # Both act on the whole history rather than on one download, which
            # is why they are here and not under `{download_id}` — and why they
            # sit above it, where a path parameter cannot swallow them.
            Route(
                "/downloads/retry",
                routes.retry_all_downloads,
                methods=["POST"],
                name="retry_all_downloads",
            ),
            Route(
                "/downloads/clear",
                routes.clear_history,
                methods=["POST"],
                name="clear_history",
            ),
            Route(
                "/downloads/{download_id}",
                routes.download_detail,
                methods=["GET"],
                name="download_detail",
            ),
            Route(
                "/downloads/{download_id}/stop",
                routes.stop_download,
                methods=["POST"],
                name="stop_download",
            ),
            Route(
                "/downloads/{download_id}/retry",
                routes.retry_download,
                methods=["POST"],
                name="retry_download",
            ),
            Route(
                "/downloads/{download_id}/move",
                routes.move_download,
                methods=["POST"],
                name="move_download",
            ),
            Route(
                "/downloads/{download_id}/events",
                routes.download_events,
                methods=["GET"],
                name="download_events",
            ),
            Route("/library", routes.library, methods=["GET"], name="library"),
            # Above `{provider}/{key}`, for the reason `/downloads/pause` sits
            # above `{download_id}`: a path parameter matches any one segment,
            # and "review" is one.
            Route(
                "/library/review",
                routes.review_selection,
                methods=["POST"],
                name="review_selection",
            ),
            # Separate from the route above, and that is the whole point of it:
            # the batch of judgements never deletes anything, and the one that
            # does is only reachable from the page that said how many and which.
            Route(
                "/library/discard",
                routes.discard_selection,
                methods=["POST"],
                name="discard_selection",
            ),
            Route(
                "/library/{provider}/{key}/review",
                routes.review_item,
                methods=["POST"],
                name="review_item",
            ),
            Route(
                "/library/{provider}/{key}/view",
                routes.library_view,
                methods=["GET"],
                name="library_view",
            ),
            Route(
                "/library/{provider}/{key}/file",
                routes.library_file,
                methods=["GET"],
                name="library_file",
            ),
            Route(
                "/library/{provider}/{key}",
                routes.library_item,
                methods=["GET"],
                name="library_item",
            ),
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
    application.state.discovery = findings
    application.state.config_path = source
    return application


async def health(request: Request) -> JSONResponse:
    """Report that the server is answering.

    Deliberately the first route. It is what proves the event loop is still
    free while a crawl is running on a worker thread, which is the property the
    whole background-job design exists to preserve.
    """
    return JSONResponse({"status": "ok"})
