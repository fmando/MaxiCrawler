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

The application holds a :class:`~maxicrawler.app.CrawlService` and nothing
else. It never reaches for a provider, a downloader or the library — a rule you
can check by reading, and that ``tests/test_api_boundaries.py`` checks by
parsing.
"""

from maxicrawler.api.errors import WebDependencyError
from maxicrawler.app import CrawlService
from maxicrawler.config import Settings

MISSING_EXTRA = (
    "the web interface needs the optional 'web' extra.\n"
    "Install it with:  uv sync --extra web\n"
    "or:               pip install 'maxicrawler[web]'"
)
"""What to say when the web dependencies are not installed."""

try:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route
except ImportError as error:  # pragma: no cover - depends on the environment
    raise WebDependencyError(MISSING_EXTRA) from error


def create_app(
    *, service: CrawlService | None = None, settings: Settings | None = None
) -> Starlette:
    """Return the MaxiCrawler web application.

    *service* is injectable so a test can drive the routes without touching a
    database or a socket. Given neither argument, the application reads the
    configuration from its default location, exactly as the CLI does.
    """
    crawl_service = service if service is not None else CrawlService(settings or Settings())
    application = Starlette(routes=[Route("/health", health, methods=["GET"])])
    application.state.crawl_service = crawl_service
    return application


async def health(request: Request) -> JSONResponse:
    """Report that the server is answering.

    Deliberately the first route. It is what proves the event loop is still
    free while a crawl is running on a worker thread, which is the property the
    whole background-job design exists to preserve.
    """
    return JSONResponse({"status": "ok"})
