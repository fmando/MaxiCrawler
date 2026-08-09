"""What each URL of the web interface answers with.

Handlers do three things and nothing else: read the request, ask a service, and
hand plain data to a template. Every decision that is not one of those lives in
:mod:`maxicrawler.api.views`, where it can be tested without a request.

The navigation names all four sections from the first page. Two of them do very
little yet, and saying so is cheaper than rearranging every page around them
later.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.templating import Jinja2Templates

from maxicrawler import __version__
from maxicrawler.api import views
from maxicrawler.api.jobs import CrawlJobs

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
"""Where the pages live. Beside the code, so an installed wheel carries them."""

STATIC_DIRECTORY = Path(__file__).parent / "static"
"""Where the one stylesheet lives."""


@dataclass(frozen=True, slots=True)
class Section:
    """One entry in the navigation."""

    name: str
    label: str
    route: str


SECTIONS = (
    Section("dashboard", "Dashboard", "dashboard"),
    Section("crawls", "Crawls", "crawls"),
    Section("library", "Library", "library"),
    Section("settings", "Settings", "settings"),
)
"""The four areas, in the order they are read."""


def jobs_of(request: Request) -> CrawlJobs:
    """Return the job registry this application is running crawls through."""
    registry: CrawlJobs = request.app.state.jobs
    return registry


def page(
    request: Request, template: str, context: dict[str, Any] | None = None, *, section: str
) -> Response:
    """Render *template* as a full page, with the chrome every page shares."""
    return TEMPLATES.TemplateResponse(
        request=request,
        name=template,
        context={
            "navigation": _navigation(request, section),
            "version": __version__,
            **(context or {}),
        },
    )


def fragment(request: Request, template: str, context: dict[str, Any]) -> Response:
    """Render *template* on its own, without the chrome.

    The same partials a page includes can be returned directly, which is what
    makes a later filter or sort a request for a fragment rather than a second
    rendering of the page in JavaScript.
    """
    return TEMPLATES.TemplateResponse(request=request, name=template, context=context)


async def dashboard(request: Request) -> Response:
    """Show what this installation has crawled."""
    crawls = jobs_of(request).service.stored_crawls(limit=20)
    return page(
        request,
        "index.html",
        {"crawls": views.crawl_rows(crawls)},
        section="dashboard",
    )


async def crawls(request: Request) -> Response:
    """List every recorded crawl.

    A stub for now, so the navigation is whole from the first page rather than
    something every later layout has to be rearranged around.
    """
    return page(
        request,
        "_placeholder.html",
        {
            "heading": "Crawls",
            "explanation": "The full list of crawls, with filtering, lands with the crawl pages.",
        },
        section="crawls",
    )


async def library(request: Request) -> Response:
    """Show what has been downloaded.

    Genuinely empty, and it will stay that way until downloads exist in this
    interface. When it does something, it will read through a service in
    :mod:`maxicrawler.app` rather than importing the library package — the same
    rule crawling follows.
    """
    return page(
        request,
        "_placeholder.html",
        {
            "heading": "Library",
            "explanation": (
                "Downloaded resources will be listed here. This interface does not "
                "download anything yet; the crawler discovers, and downloading stays a "
                "separate pipeline."
            ),
        },
        section="library",
    )


async def settings(request: Request) -> Response:
    """Show the configuration this server is running with."""
    return page(
        request,
        "_placeholder.html",
        {
            "heading": "Settings",
            "explanation": "The effective configuration lands with the remaining pages.",
        },
        section="settings",
    )


def _navigation(request: Request, active: str) -> tuple[dict[str, Any], ...]:
    """Return the navigation, with one entry marked as the page you are on.

    Paths rather than whole URLs. `url_for` builds an absolute one, which bakes
    this server's scheme and host into every page -- wrong the moment anything
    sits in front of it, and pointless for links to itself.
    """
    return tuple(
        {
            "label": section.label,
            "url": request.url_for(section.route).path,
            "active": section.name == active,
        }
        for section in SECTIONS
    )
