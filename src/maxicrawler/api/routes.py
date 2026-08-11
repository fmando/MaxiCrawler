"""What each URL of the web interface answers with.

Handlers do three things and nothing else: read the request, ask a service, and
hand plain data to a template. Every decision that is not one of those lives in
:mod:`maxicrawler.api.views`, where it can be tested without a request.

The navigation names every section from the first page, including the ones that
do very little yet — saying so is cheaper than rearranging every page around
them later. Downloads joined them in Sprint 15, when there was finally a queue
to put on such a page.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.templating import Jinja2Templates

from maxicrawler import __version__
from maxicrawler.api import stream, views
from maxicrawler.api.downloads import Accepted, DownloadRun, Move, TransferQueue
from maxicrawler.api.errors import QueueFullError
from maxicrawler.api.jobs import DEFAULT_RETAINED_JOBS, CrawlJob, CrawlJobs
from maxicrawler.app import (
    DEFAULT_LINKS_PER_PAGE,
    DEFAULT_PAGES_PER_PAGE,
    DEFAULT_PER_PAGE,
    DiscoveryService,
    LibraryQuery,
    LibraryService,
    LibrarySort,
    LinkQuery,
    LinkSort,
    Matches,
    PageQuery,
    PageState,
    StoredPayload,
    TargetKind,
    browse_pages,
    crawl_document,
)
from maxicrawler.app.viewing import DOWNLOAD_CONTENT_TYPE
from maxicrawler.domain import DownloadStatus
from maxicrawler.web.report import CrawlReport

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
    Section("downloads", "Downloads", "downloads"),
    Section("library", "Library", "library"),
    Section("settings", "Settings", "settings"),
)
"""The five areas, in the order the chain runs through them.

Downloads became one of them in Sprint 15. Before the queue there was nothing to
put on such a page — a transfer had its own page and there was only ever one —
and a section for it would have been a heading over a single link.
"""


def jobs_of(request: Request) -> CrawlJobs:
    """Return the job registry this application is running crawls through."""
    registry: CrawlJobs = request.app.state.jobs
    return registry


def downloads_of(request: Request) -> TransferQueue:
    """Return the queue this application is running downloads through."""
    queue: TransferQueue = request.app.state.downloads
    return queue


def library_of(request: Request) -> LibraryService:
    """Return the service this application reads the library through."""
    service: LibraryService = request.app.state.library
    return service


def discovery_of(request: Request) -> DiscoveryService:
    """Return the service this application reads crawl findings through."""
    service: DiscoveryService = request.app.state.discovery
    return service


def _running(request: Request) -> dict[str, Any] | None:
    """Return the transfer running right now, for the pages that mention it.

    One line on the dashboard and above the library, so navigating away from a
    download does not mean losing it. ``None`` when nothing is running, which is
    most of the time.
    """
    run = downloads_of(request).active()
    return None if run is None else views.download_view(run.snapshot())


def _download(request: Request) -> DownloadRun:
    """Return the download this request addresses.

    Raises:
        HTTPException: this process does not hold that download. Which includes
            one it ran and has since evicted — the registry is a live view, and
            a finished download's record is the library.
    """
    run = downloads_of(request).get(request.path_params["download_id"])
    if run is None:
        raise HTTPException(status_code=404, detail="no such download")
    return run


def _job(request: Request) -> CrawlJob:
    """Return the crawl this request addresses.

    Raises:
        HTTPException: this process does not hold that crawl. Which includes
            one it ran and has since evicted — the registry is a live view, not
            the record.
    """
    job = jobs_of(request).get(request.path_params["job_id"])
    if job is None:
        raise HTTPException(status_code=404, detail="no such crawl")
    return job


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
            below_seed=values["below_seed"],
            respect_robots=values["respect_robots"],
        )
    except ValueError as error:
        # The values they typed come back with the message. Losing a pasted URL
        # because the depth was wrong is a small rudeness that adds up.
        return _dashboard(request, form_values=values, error=str(error), status=400)
    job = jobs.submit(session)
    return RedirectResponse(url=f"/crawls/{job.id}", status_code=303)


async def crawl_detail(request: Request) -> Response:
    """Show one crawl as it stands, or everything it found once it is over.

    Rendered by the server on every request, so a reload is a complete way to
    follow a crawl. What a live stream adds is convenience, not capability.

    A crawl this process never ran is answered from the database instead. Not
    an edge case: after a restart it is *every* crawl, and a list whose links
    all lead to "no such crawl" would be a list not worth having.
    """
    job = jobs_of(request).get(request.path_params["job_id"])
    if job is None:
        return _recorded_crawl(request)
    context: dict[str, Any] = {"crawl": views.progress_view(job.snapshot())}
    report = job.report
    if report is not None:
        context["report"] = views.report_view(report)
        context["pages"] = _page_table(request, report)
        context["links"] = _link_table(
            request, report.session.session_id, discovered=report.summary.unique_urls
        )
    return page(request, "crawl.html", context, section="crawls")


async def crawl_json(request: Request) -> Response:
    """Answer with the whole report as JSON.

    The same document ``maxicrawler crawl --json`` prints, because it is the
    same function. A page shows two hundred rows of a crawl that found
    thousands; this is where the rest of them are.
    """
    jobs = jobs_of(request)
    job_id = request.path_params["job_id"]
    job = jobs.get(job_id)
    if job is None:
        return _recorded_crawl_json(jobs, job_id)
    report = job.report
    if report is not None:
        return JSONResponse(crawl_document(report))
    # No report, for one of two reasons, and the same answer serves both: a
    # crawl still running has not written one yet, and a crawl whose seed could
    # not be read never will. `finished` is what tells a client which it is,
    # and so whether asking again is worth anything.
    snapshot = job.snapshot()
    return JSONResponse(
        {
            "session_id": job.id,
            "seed_url": snapshot.seed_url,
            "state": str(snapshot.state),
            "finished": snapshot.is_finished,
            "error": snapshot.error,
            "detail": snapshot.error or "the crawl has not finished",
        },
        status_code=409,
    )


async def crawl_events(request: Request) -> Response:
    """Stream one crawl's progress until it ends.

    Server-sent events rather than WebSockets: the traffic runs one way, and a
    browser reconnects on its own without anything being written for it.
    """
    job = _job(request)
    return StreamingResponse(
        stream.crawl_stream(job),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Nginx buffers proxied responses by default, which for a stream
            # means it arrives all at once when the crawl is already over.
            "X-Accel-Buffering": "no",
        },
    )


async def stop_crawl(request: Request) -> Response:
    """Ask one crawl to stop, and show the page again.

    A plain form and a redirect, so the button works with scripting switched
    off. Stopping is not instant -- the engine finishes the page it is on --
    and the page says so rather than pretending the click was the end of it.
    """
    job = _job(request)
    job.stop()
    return RedirectResponse(url=f"/crawls/{job.id}", status_code=303)


async def downloads(request: Request) -> Response:
    """Show the queue: what is running, what is waiting, and what became of the rest.

    One page rather than a list of links to pages, because the question it
    answers is about the *set* — how much is left, is anything stuck, is the
    order right. Every control on it is a form, so it works with scripting off.

    Live without a stream of its own. The running transfer's own event stream
    is embedded, and ``download.js`` reloads the page when that transfer ends —
    which is exactly when the rest of this page changes. A queue nobody is
    draining has nothing to stream, and a page that reloaded on a timer would
    fight whoever is reading it.
    """
    queue = downloads_of(request)
    snapshot = queue.snapshot()
    return page(
        request,
        "downloads.html",
        {"queue": views.queue_view(snapshot, limit=queue.limit)},
        section="downloads",
    )


async def start_download(request: Request) -> Response:
    """Put one link in the queue, and send the browser to watch it.

    The link arrives in a form body rather than in a query string, which is not
    only about it being an action: a share link keeps its decryption key in the
    URL fragment, and a fragment is the one part of a URL a browser never sends.
    In a field it survives the round trip; in a link it would be lost, and in a
    query string it would be written into a log.

    Answers with a redirect, so reloading the download afterwards does not offer
    to queue it a second time.
    """
    form = await read_form(request)
    downloads = downloads_of(request)
    try:
        run = downloads.submit(form.get("url", ""))
    except ValueError as error:
        return _refuse_download(request, str(error), status=400)
    except QueueFullError as error:
        return _refuse_download(request, str(error), status=409)
    return RedirectResponse(url=f"/downloads/{run.id}", status_code=303)


async def queue_selection(request: Request) -> Response:
    """Queue the links that were ticked, and show the queue.

    The URLs arrive in the body, one field per ticked row, for the reason a
    single one does: a share link keeps its decryption key in the fragment, and
    a fragment is the one part of a URL a browser never sends in a link.

    Partial by design. A selection where two links are malformed is not a
    refusal of the other ninety-eight — the queue takes what it can and the
    page says what it did.
    """
    form = await read_forms(request)
    accepted = downloads_of(request).submit_all(form.get("url", ()))
    if accepted.queued == 0 and not accepted.is_whole:
        return _refuse_download(request, _nothing_queued(accepted), status=409)
    return RedirectResponse(url=_queued_url(accepted), status_code=303)


async def queue_matches(request: Request) -> Response:
    """Queue every fetchable link the current filter matches, and show the queue.

    The one control this sprint exists for: a filtered report is a set somebody
    has already decided on, and ticking two hundred boxes to say so again is the
    work this replaces.

    Nothing but the query travels. The browser sends the filter it is looking
    at, the server resolves it against what the crawl recorded, and the URLs —
    decryption keys and all — never leave this process.
    """
    session_id = request.path_params["job_id"]
    downloads = downloads_of(request)
    matches = discovery_of(request).fetchable(
        session_id, _link_query(request), limit=downloads.room()
    )
    if not matches.urls:
        return _refuse_download(request, _nothing_matched(matches), status=409)
    accepted = downloads.submit_all(matches.urls)
    return RedirectResponse(url=_queued_url(accepted), status_code=303)


def _queued_url(accepted: Accepted) -> str:
    """Return where to land after queueing a batch.

    One link goes to its own page, because that is what somebody who queued one
    link wants to watch. Several go to the queue, because that is the thing they
    just changed.
    """
    if accepted.queued == 1 and accepted.is_whole:
        return f"/downloads/{accepted.runs[0].id}"
    return "/downloads"


def _nothing_queued(accepted: Accepted) -> str:
    """Return why a selection produced no downloads at all."""
    if accepted.no_room:
        return f"the queue had no room for any of the {accepted.no_room} links selected"
    return f"none of the {accepted.rejected} links selected is an absolute HTTP or HTTPS URL"


def _nothing_matched(matches: Matches) -> str:
    """Return why a filter produced no downloads at all."""
    if matches.total:
        return (
            f"{matches.total} links match, and the queue has no room for any of them. "
            "Let some finish, or cancel what you no longer want."
        )
    return "nothing this filter matches can be downloaded by the providers installed here"


async def download_detail(request: Request) -> Response:
    """Show one download as it stands, or what became of it.

    Rendered by the server on every request, so a reload is a complete way to
    follow a transfer. What the live stream adds is convenience, not capability.
    """
    run = _download(request)
    downloads = downloads_of(request)
    return page(
        request,
        "download.html",
        {
            "download": views.download_view(
                run.snapshot(),
                position=downloads.position_of(run.id),
                is_paused=downloads.is_paused,
            )
        },
        section="downloads",
    )


async def stop_download(request: Request) -> Response:
    """Ask one transfer to stop, or take a waiting one out of the queue.

    One button for both, because they are one intention: the person clicking it
    wants this download not to happen. What that costs differs — a waiting
    request never started, a running one stops within a chunk — and neither
    leaves anything behind in the library.

    The same shape as stopping a crawl, deliberately: a person clicking Stop
    should not have to know which half of the chain they are looking at.
    """
    run = _download(request)
    downloads_of(request).cancel(run.id)
    return RedirectResponse(url=_back_to(request, f"/downloads/{run.id}"), status_code=303)


async def retry_download(request: Request) -> Response:
    """Queue the same link again, and show the new request.

    A new entry rather than a reset of the old one: what happened the first time
    is a fact, and a history that overwrote its own failures would be one nobody
    could read.

    From one download's page that means the new download's page, which is the
    thing just asked for. From the queue it means the queue, where the new entry
    is visible at the end of the line along with everything else waiting.
    """
    run = _download(request)
    try:
        again = downloads_of(request).retry(run.id)
    except QueueFullError as error:
        return _refuse_download(request, str(error), status=409)
    if again is None:
        return _refuse_download(request, RETRY_UNFINISHED, status=409)
    return RedirectResponse(url=_back_to(request, f"/downloads/{again.id}"), status_code=303)


RETRY_UNFINISHED = "that download has not finished, so there is nothing to retry"
"""Said to whoever asked to retry something that is still going."""


async def move_download(request: Request) -> Response:
    """Move one waiting request within the queue, and show the page again."""
    run = _download(request)
    form = await read_form(request)
    where = _move(form.get("where"))
    if where is not None:
        downloads_of(request).move(run.id, where)
    return RedirectResponse(url=_back_to(request, f"/downloads/{run.id}"), status_code=303)


def _move(value: str | None) -> Move | None:
    """Return the move *value* names, or ``None`` for anything else."""
    try:
        return Move(value or "")
    except ValueError:
        return None


async def pause_downloads(request: Request) -> Response:
    """Hold the queue, or let it go again, and show the page again.

    One route for both directions, because the button is one button and its
    label is whichever state it would leave. A form field says which was meant,
    so a stale page cannot pause a queue somebody has already resumed.
    """
    form = await read_form(request)
    downloads = downloads_of(request)
    if form.get("paused") == "1":
        downloads.pause()
    else:
        downloads.resume()
    return RedirectResponse(url=_back_to(request, "/downloads"), status_code=303)


def _back_to(request: Request, default: str) -> str:
    """Return where the form's action said to go afterwards, or *default*.

    A parameter on the action rather than the ``Referer`` header: the same
    button sits on the queue and on one download's page, and a header a browser
    may withhold is not a thing to route on. Only paths of our own are honoured,
    so this cannot be turned into an open redirect.
    """
    asked = request.query_params.get("back", "")
    return asked if asked.startswith("/") and not asked.startswith("//") else default


async def download_events(request: Request) -> Response:
    """Stream one download's progress until it ends."""
    run = _download(request)
    return StreamingResponse(
        stream.download_stream(run),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _refuse_download(request: Request, message: str, *, status: int) -> Response:
    """Say why a download was not started, on a page of its own.

    A short page rather than the report the button was on. Re-rendering that
    report would mean running the crawl's query again to say one sentence, and
    the browser's own Back button is a better way back to it than a rebuilt
    copy.
    """
    active = downloads_of(request).active()
    response = page(
        request,
        "download_refused.html",
        {"message": message, "active_id": None if active is None else active.id},
        section="downloads",
    )
    response.status_code = status
    return response


async def crawls(request: Request) -> Response:
    """List every recorded crawl, newest first."""
    jobs = jobs_of(request)
    return page(
        request,
        "crawls.html",
        {"crawls": views.crawl_rows(jobs.service.stored_crawls(limit=None), live=_live(jobs))},
        section="crawls",
    )


async def library(request: Request) -> Response:
    """Show what has been downloaded, as the query string asks for it.

    Read through :class:`~maxicrawler.app.LibraryService`, not by opening the
    library here. That is the same rule crawling follows, and the reason this
    page can search, sort and page real files while ``api`` imports neither
    ``library`` nor ``downloader`` nor ``providers``.

    Every parameter is read leniently. A search, a sort and a page number arrive
    from a form, a bookmark or a typed URL, and a listing in the default order is
    a better answer to a stale link than a refusal.
    """
    service = library_of(request)
    return page(
        request,
        "library.html",
        {
            "library": views.library_view(service.browse(_query(request))),
            "library_path": service.library_root.as_posix(),
            "running": _running(request),
        },
        section="library",
    )


async def library_item(request: Request) -> Response:
    """Show everything one stored file is known to be.

    Raises:
        HTTPException: nothing here is addressed by those two names — which
            covers a key that could not be a directory name, a directory that is
            not there, and metadata that cannot be read. One answer for all
            three, because telling them apart would only tell whoever asked
            which of them they had guessed.
    """
    service = library_of(request)
    item = service.item(request.path_params["provider"], request.path_params["key"])
    if item is None:
        raise HTTPException(status_code=404, detail="no such file")
    payload = service.payload(item.directory, item.key)
    return page(
        request,
        "library_item.html",
        {"item": views.item_view(item, payload)},
        section="library",
    )


async def library_file(request: Request) -> Response:
    """Answer with the stored bytes, as a download.

    Always an attachment and always ``application/octet-stream``: this route
    states no type, so no browser gets to decide to render what it receives.
    Showing a file is a different route, with a different answer to that
    question.
    """
    payload = _payload(request)
    return FileResponse(
        payload.path,
        filename=payload.filename,
        media_type=DOWNLOAD_CONTENT_TYPE,
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": "nosniff"},
    )


VIEW_HEADERS = {
    # A `.txt` must not be re-interpreted as HTML by a browser that thinks it
    # knows better, and no URL of ours travels outward in a referrer. Both are
    # true of every type, so both are unconditional.
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
"""What every inline answer carries."""

SANDBOX_POLICY = "sandbox; default-src 'none'; frame-ancestors 'self'"
"""What an inline answer that could execute script carries as well.

``sandbox`` is the directive that matters: the browser treats the response as its
own opaque origin, so a stored HTML page or SVG cannot reach the interface that
served it. Without it, showing somebody's downloaded HTML inline would hand that
HTML every power this unauthenticated interface has — reading the settings page,
starting a crawl, starting a download.

It is **not** sent for the other types, and that is a finding rather than an
omission: Chrome refuses to render a PDF under it (``ERR_BLOCKED_BY_CLIENT``),
because the directive blocks the plugin its viewer is. A PDF, an image and plain
text cannot execute script in our origin in the first place — a PDF's own script
runs inside the browser's viewer, not in the page that framed it — so the policy
would cost the whole feature and buy nothing. See ADR-027.
"""


async def library_view(request: Request) -> Response:
    """Answer with the stored bytes for a browser to display.

    The only route that states what a file *is*, and it does so only for the
    types :mod:`maxicrawler.app.viewing` allows — MaxiCrawler renders nothing
    itself, converts nothing, and interprets nothing.

    Raises:
        HTTPException: there is no such file, or nothing here can show it. The
            second is 415 with the reason: the resource exists, and what is
            missing is a representation a browser could be handed.
    """
    payload = _payload(request)
    media = payload.media
    if not media.can_display:
        raise HTTPException(status_code=415, detail=media.reason or "cannot be displayed")
    headers = dict(VIEW_HEADERS)
    if media.is_script_capable:
        headers["Content-Security-Policy"] = SANDBOX_POLICY
    return FileResponse(
        payload.path,
        filename=payload.filename,
        media_type=media.content_type,
        content_disposition_type="inline",
        headers=headers,
    )


def _payload(request: Request) -> StoredPayload:
    """Return the file this request addresses.

    Raises:
        HTTPException: there is no such entry, or the record claims a file that
            is not on disk. The second is the reason this asks the service
            rather than joining a path: a library is repairable, and a response
            promising bytes that are gone would not be.
    """
    payload = library_of(request).payload(
        request.path_params["provider"], request.path_params["key"]
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="no such file")
    return payload


def _query(request: Request) -> LibraryQuery:
    """Return the library query this request asks for."""
    values = request.query_params
    return LibraryQuery(
        search=values.get("q", "").strip(),
        provider=values.get("provider") or None,
        status=_status(values.get("status")),
        sort=LibrarySort.parse(values.get("sort"), default=LibraryQuery().sort),
        descending=values.get("dir", "desc") != "asc",
        page=_positive(values.get("page"), default=1),
        per_page=_positive(values.get("per_page"), default=DEFAULT_PER_PAGE),
    )


def _status(value: str | None) -> DownloadStatus | None:
    """Return the status *value* names, or ``None`` for anything else.

    A status nobody recognises filters nothing rather than refusing the page,
    which is the same leniency the sort order is read with.
    """
    try:
        return DownloadStatus(value or "")
    except ValueError:
        return None


def _positive(value: str | None, *, default: int) -> int:
    """Return a positive whole number, or *default* for anything else."""
    try:
        number = int(value or "")
    except ValueError:
        return default
    return number if number > 0 else default


async def settings(request: Request) -> Response:
    """Show the configuration this server is running with.

    Read-only, and the page says so. Writing configuration from a browser is a
    different feature with different questions — which file, whose permissions,
    what happens to a crawl already running under the old values — and pretending
    otherwise with an editable-looking form would be the wrong promise.
    """
    effective = jobs_of(request).service.settings
    source = request.app.state.config_path
    return page(
        request,
        "settings.html",
        {
            "groups": views.settings_view(effective),
            "toml": effective.to_toml(),
            "source": None if source is None else str(source),
            "source_exists": source is not None and Path(source).exists(),
        },
        section="settings",
    )


def _live(jobs: CrawlJobs) -> frozenset[str]:
    """Return the crawls this process is running right now."""
    return frozenset(
        job.id for job in jobs.recent(limit=DEFAULT_RETAINED_JOBS) if not job.snapshot().is_finished
    )


def _recorded_crawl(request: Request) -> Response:
    """Render a crawl from the database, for one this process never ran.

    Raises:
        HTTPException: nothing knows that crawl, not even the record.
    """
    jobs = jobs_of(request)
    job_id = request.path_params["job_id"]
    stored = jobs.service.stored_crawl(job_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="no such crawl")
    return page(
        request,
        "recorded.html",
        {
            "crawl": views.stored_view(stored),
            "links": _link_table(request, job_id, discovered=stored.links_discovered),
        },
        section="crawls",
    )


def _link_table(request: Request, session_id: str, *, discovered: int) -> dict[str, Any]:
    """Return the discovered-link table, as the query string asks for it.

    The whole question — which URLs, in which order, which page of them, and
    which of them this installation could fetch — is one call on
    :class:`~maxicrawler.app.DiscoveryService`. Whether a link can be downloaded
    is answered from the URL alone: a plugin classifies it, a provider claims it,
    and the provider says whether it was composed with everything a transfer
    needs. No request is made, so a page of links costs nothing to render.

    Every parameter is read leniently, the same way the library reads its own.
    A report arrives from a bookmark, a shared link or a typed URL, and the
    default listing is a better answer to a stale one than a refusal.
    """
    page = discovery_of(request).browse(session_id, _link_query(request), discovered=discovered)
    return views.link_view(
        page,
        base=f"/crawls/{session_id}",
        hidden=_hidden_columns(request),
        carry=_carry(request, views.LINK_PARAMS),
        downloads_everything=downloads_of(request).service.downloads_ordinary_urls(),
    )


def _page_table(request: Request, report: CrawlReport) -> dict[str, Any]:
    """Return the table of pages the crawl reached, as the query string asks.

    Read from the report rather than from a database, because per-page outcomes
    are not written down: what this shows exists only while the process that ran
    the crawl is alive. Which is also why the page of a crawl only the database
    remembers has no such table, and says so instead.
    """
    return views.page_view(
        browse_pages(report.pages, _page_query(request)),
        base=f"/crawls/{report.session.session_id}",
        carry=_carry(request, views.PAGE_PARAMS),
    )


def _page_query(request: Request) -> PageQuery:
    """Return the page-table query this request asks for."""
    values = request.query_params
    return PageQuery(
        search=values.get("pq", "").strip(),
        state=PageState.parse(values.get("pstate")),
        page=_positive(values.get("ppage"), default=1),
        per_page=DEFAULT_PAGES_PER_PAGE,
    )


def _carry(request: Request, owned: frozenset[str]) -> dict[str, str]:
    """Return the query parameters that are not *owned* by the table asking.

    Both tables of a report live on one URL, so each builds its links from its
    own parameters *plus* whatever the other left there. Without this, filtering
    the pages would quietly discard the link filter on screen — and a filter
    that undoes another filter is one nobody keeps using.
    """
    return {
        name: value for name, value in request.query_params.items() if name not in owned and value
    }


def _link_query(request: Request) -> LinkQuery:
    """Return the link query this request asks for."""
    values = request.query_params
    return LinkQuery(
        search=values.get("q", "").strip(),
        plugin=values.get("plugin") or None,
        category=values.get("category") or None,
        target=_target(values.get("target")),
        # Read as written rather than parsed into a state. The sentinel for
        # "in none of them" is not a member, and a value naming a state this
        # installation cannot answer is handled by the service — which filters
        # nothing rather than everything, so a bookmark that predates a resolver
        # shows the crawl instead of an empty table.
        state=values.get("state") or None,
        downloadable=_downloadable(values.get("dl")),
        normalized_only=values.get("norm") == "1",
        sort=LinkSort.parse(values.get("sort"), default=LinkQuery().sort),
        descending=values.get("dir") == "desc",
        page=_positive(values.get("page"), default=1),
        per_page=DEFAULT_LINKS_PER_PAGE,
    )


def _target(value: str | None) -> TargetKind | None:
    """Return the target kind *value* names, or ``None`` for anything else."""
    try:
        return TargetKind(value or "")
    except ValueError:
        return None


def _downloadable(value: str | None) -> bool | None:
    """Return which side of the downloadable filter *value* asks for.

    Anything but the two words filters nothing, rather than being read as one
    of them: "no" and "nonsense" must not mean the same thing.
    """
    if value == "yes":
        return True
    return False if value == "no" else None


def _hidden_columns(request: Request) -> frozenset[str]:
    """Return which columns of the link table this request wants left out.

    Unknown names are dropped rather than refused. The list is written by our
    own links, so anything else arrived from an older bookmark or a hand-typed
    URL, and neither deserves an error page.
    """
    asked = request.query_params.get("hide", "")
    known = {column.name for column in views.LINK_COLUMNS}
    return frozenset(name for name in asked.split(",") if name in known)


def _recorded_crawl_json(jobs: CrawlJobs, job_id: str) -> Response:
    """Refuse a document for a crawl only the database remembers.

    Raises:
        HTTPException: nothing knows that crawl at all.

    The record holds a summary and the URLs, never the per-page outcomes the
    document is largely made of. Answering with a document missing half its
    fields would be worse than saying so.
    """
    stored = jobs.service.stored_crawl(job_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="no such crawl")
    return JSONResponse(
        {
            "session_id": stored.session_id,
            "seed_url": stored.seed_url,
            "state": str(stored.state),
            "finished": stored.finished_at is not None,
            "error": None,
            "detail": (
                "this server did not run that crawl, so only the recorded summary "
                "exists; the full document is kept in memory by the process that ran it"
            ),
        },
        status_code=409,
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
            "crawls": views.crawl_rows(jobs.service.stored_crawls(limit=20), live=_live(jobs)),
            "form": {**(form_values or _default_form(jobs)), "action": "/crawls", "error": error},
            "running": _running(request),
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
        "below_seed": settings.crawl_below_seed,
        "respect_robots": settings.respect_robots,
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
    return {name: values[-1] for name, values in (await read_forms(request)).items()}


async def read_forms(request: Request) -> dict[str, list[str]]:
    """Return the fields of a submitted form, keeping repeated names.

    What :func:`read_form` is built on, and what a batch of ticked checkboxes
    needs: they all submit under one name, and the last one is not the answer.

    Raises:
        HTTPException: the body was not an urlencoded form. Saying so beats
            quietly seeing no fields at all.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=415, detail="expected an urlencoded form")
    return parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)


def _submitted(form: dict[str, str]) -> dict[str, Any]:
    """Return what was typed, so a rejected form can show it again."""
    return {
        "url": form.get("url", "").strip(),
        "depth": form.get("depth", "").strip(),
        "max_pages": form.get("max_pages", "").strip(),
        "same_domain": bool(form.get("same_domain")),
        "below_seed": bool(form.get("below_seed")),
        "respect_robots": bool(form.get("respect_robots")),
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
