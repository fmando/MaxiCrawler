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
from urllib.parse import parse_qs

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
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
    """Show the form, and what this installation has crawled."""
    return _dashboard(request)


async def start_crawl(request: Request) -> Response:
    """Start a crawl from the form and send the browser to watch it.

    Answers with a redirect rather than the page itself, so reloading the crawl
    afterwards does not offer to start it a second time.
    """
    form = await read_form(request)
    values = _submitted(form)
    jobs = jobs_of(request)
    try:
        session = jobs.service.build_session(
            values["url"],
            depth=_whole_number(form, "depth"),
            max_pages=_whole_number(form, "max_pages"),
            same_domain=values["same_domain"],
        )
    except ValueError as error:
        # The values they typed come back with the message. Losing a pasted URL
        # because the depth was wrong is a small rudeness that adds up.
        return _dashboard(request, form_values=values, error=str(error), status=400)
    job = jobs.submit(session)
    return RedirectResponse(url=f"/crawls/{job.id}", status_code=303)


async def crawl_detail(request: Request) -> Response:
    """Show one crawl as it stands.

    Rendered by the server on every request, so a reload is a complete way to
    follow a crawl. What a live stream adds is convenience, not capability.
    """
    job = jobs_of(request).get(request.path_params["job_id"])
    if job is None:
        raise HTTPException(status_code=404, detail="no such crawl")
    return page(
        request,
        "crawl.html",
        {"crawl": views.progress_view(job.snapshot())},
        section="crawls",
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


def _dashboard(
    request: Request,
    *,
    form_values: dict[str, Any] | None = None,
    error: str | None = None,
    status: int = 200,
) -> Response:
    """Render the dashboard, with the form in whatever state it is in."""
    jobs = jobs_of(request)
    response = page(
        request,
        "index.html",
        {
            "crawls": views.crawl_rows(jobs.service.stored_crawls(limit=20)),
            "form": {**(form_values or _default_form(jobs)), "action": "/crawls", "error": error},
        },
        section="dashboard",
    )
    response.status_code = status
    return response


def _default_form(jobs: CrawlJobs) -> dict[str, Any]:
    """Return the empty form, filled from the configuration.

    The same defaults the CLI applies, read through the same service, so the
    two cannot answer differently for a crawl nobody customised.
    """
    settings = jobs.service.settings
    return {
        "url": "",
        "depth": settings.crawl_depth,
        "max_pages": settings.crawl_max_pages,
        "same_domain": settings.crawl_same_domain,
    }


async def read_form(request: Request) -> dict[str, str]:
    """Return the fields of a submitted form.

    Parsed with :func:`urllib.parse.parse_qs` rather than through Starlette,
    which needs ``python-multipart`` even for a form of four text boxes. That
    package exists for file uploads and multipart bodies; this interface has
    neither and is not going to grow them by accident, so the standard
    library's own parser for the one content type in play is both smaller and
    exactly as correct.

    Raises:
        HTTPException: the body was not an urlencoded form. Saying so beats
            quietly seeing no fields at all.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=415, detail="expected an urlencoded form")
    fields = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    return {name: values[-1] for name, values in fields.items()}


def _submitted(form: dict[str, str]) -> dict[str, Any]:
    """Return what was typed, so a rejected form can show it again."""
    return {
        "url": form.get("url", "").strip(),
        "depth": form.get("depth", "").strip(),
        "max_pages": form.get("max_pages", "").strip(),
        "same_domain": bool(form.get("same_domain")),
    }


def _whole_number(form: dict[str, str], name: str) -> int | None:
    """Return an integer field, or ``None`` when it was left empty.

    Raises:
        ValueError: the field held something that is not a whole number. The
            message names the field, because "invalid literal for int()" tells
            a person nothing about which box to look at.
    """
    raw = form.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        msg = f"{name.replace('_', ' ')} must be a whole number"
        raise ValueError(msg) from None


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
