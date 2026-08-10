"""Turning a crawl into something a template can render.

Every function here is pure. Nothing touches HTTP, a database or a socket, so
the whole presentation layer is testable the way
:func:`maxicrawler.cli.crawling.render_crawl` is: give it a value, read the
result.

Templates receive plain dicts and tuples rather than domain objects. That is
not ceremony — it is what keeps a template from quietly growing logic. A
template that can only read strings and numbers cannot decide anything, so
every decision stays here where a test can reach it.

**Wording lives here, not in a shared module.** The terminal and a browser
legitimately say things differently: `render_crawl` writes "stopped at the page
limit" on its own line, while a page shows a short badge. Sharing the *numbers*
is what matters, and those come from the same report either way.
"""

from collections.abc import Container, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from maxicrawler.api.downloads import DownloadSnapshot
from maxicrawler.api.jobs import JobSnapshot
from maxicrawler.app import (
    DownloadProgress,
    DownloadSummary,
    LibraryItem,
    LibraryPage,
    LibraryQuery,
    LibrarySort,
    StoredPayload,
)
from maxicrawler.config import Settings
from maxicrawler.crawler import PluginUsage
from maxicrawler.database import StoredCrawl, StoredUrl
from maxicrawler.domain import DownloadStatus
from maxicrawler.plugins.generic import GENERIC_PLUGIN_NAME
from maxicrawler.utils import format_size
from maxicrawler.web.models import LinkKind
from maxicrawler.web.report import CrawlReport, PageOutcome, SkipReason
from maxicrawler.web.session import CrawlOptions, CrawlState

ABANDONED_LABEL = "abandoned"
"""What a crawl the database left unfinished and nobody is running is called.

Not a :class:`~maxicrawler.web.session.CrawlState`, because nothing ever
*enters* this state: it is what an unfinished record means once you know no
process is behind it. The engine cannot write it, since a process that is being
killed does not get to update a row on its way out.
"""

MAX_LISTED_PAGES = 200
"""How many pages a report lists before saying how many it left out."""

MAX_LISTED_LINKS = 200
"""How many discovered URLs a report lists before saying the same.

A crawl of fifty pages routinely finds thousands. A table that long is not a
report, and the JSON document beside it is the right answer for anyone who
wants all of them.
"""

STATE_LABELS: dict[CrawlState, str] = {
    CrawlState.PENDING: "queued",
    CrawlState.RUNNING: "running",
    CrawlState.COMPLETED: "completed",
    CrawlState.PAGE_LIMIT: "page limit",
    CrawlState.INTERRUPTED: "stopped",
}
"""Short enough to sit in a badge; the CLI says the same things at more length."""

STATE_TONES: dict[CrawlState, str] = {
    CrawlState.PENDING: "idle",
    CrawlState.RUNNING: "busy",
    CrawlState.COMPLETED: "good",
    CrawlState.PAGE_LIMIT: "warn",
    CrawlState.INTERRUPTED: "warn",
}
"""Which of the four style classes a state gets. Colour is decided in CSS."""

MAX_LISTED_DOWNLOADS = 200
"""How many stored resources the library page lists before saying the rest."""

STATUS_LABELS: dict[DownloadStatus, str] = {
    DownloadStatus.PENDING: "starting",
    DownloadStatus.RUNNING: "downloading",
    DownloadStatus.COMPLETED: "completed",
    DownloadStatus.SKIPPED: "already stored",
    DownloadStatus.FAILED: "failed",
}
"""A skipped download is not a lesser success; it is one that needed no bytes."""

STATUS_TONES: dict[DownloadStatus, str] = {
    DownloadStatus.PENDING: "idle",
    DownloadStatus.RUNNING: "busy",
    DownloadStatus.COMPLETED: "good",
    DownloadStatus.SKIPPED: "good",
    DownloadStatus.FAILED: "bad",
}

KIND_LABELS: dict[LinkKind, str] = {
    LinkKind.ANCHOR: "anchor",
    LinkKind.IMAGE: "image",
    LinkKind.SCRIPT: "script",
    LinkKind.STYLESHEET: "stylesheet",
    LinkKind.FRAME: "frame",
    LinkKind.REDIRECT: "meta refresh",
    LinkKind.TEXT: "plain text",
}


def format_number(value: int) -> str:
    """Return *value* with thousands separated, because these get large."""
    return f"{value:,}"


def format_duration(seconds: float) -> str:
    """Return a readable length of time.

    Seconds below a minute, because that is where most crawls live; minutes and
    hours above it, because "412.7 s" is a number nobody converts in their head.
    """
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, remaining = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes} min {remaining:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def format_timestamp(moment: datetime) -> str:
    """Return a moment as a person reading a table wants it: short and sortable."""
    return moment.strftime("%Y-%m-%d %H:%M")


def describe_scope(options: CrawlOptions) -> str:
    """Return what a crawl was allowed to reach, in one phrase."""
    if not options.same_domain:
        return "any domain"
    return "same domain and subdomains" if options.include_subdomains else "same domain"


def describe_options(options: CrawlOptions) -> str:
    """Return the one line that says what a crawl was told to do."""
    return (
        f"depth {options.max_depth} · {describe_scope(options)} · "
        f"max {format_number(options.max_pages)} pages"
    )


@dataclass(frozen=True, slots=True)
class PluginShare:
    """How much of a crawl's discovery one plugin accounted for."""

    name: str
    count: int
    share: float
    """Between 0 and 1, of every classified URL."""

    is_fallback: bool
    """Whether this is the generic plugin rather than a host-specific one."""

    @property
    def percent(self) -> str:
        """Return the share as a rounded percentage."""
        return f"{self.share * 100:.0f}%"

    @property
    def count_label(self) -> str:
        """Return the count as the page prints it."""
        return format_number(self.count)

    @property
    def width(self) -> str:
        """Return the bar width as a CSS percentage.

        Not rounded to whole percent like the label: a plugin accounting for
        four URLs in ten thousand would otherwise draw a bar of nothing, and a
        bar that is invisible says "none" where the table says "4".
        """
        return f"{max(self.share * 100, 0.4):.2f}%" if self.count else "0%"


def plugin_shares(usage: Iterable[PluginUsage]) -> tuple[PluginShare, ...]:
    """Return the plugin distribution, the interesting plugins first.

    Host-specific plugins come before the generic fallback whatever the counts
    say. On a page full of share links the generic plugin will always win by
    volume, and burying "mega: 1291" underneath it would hide the one line this
    project exists to produce.
    """
    entries = tuple(usage)
    total = sum(entry.count for entry in entries)
    shares = [
        PluginShare(
            name=entry.name,
            count=entry.count,
            share=entry.count / total if total else 0.0,
            is_fallback=entry.name == GENERIC_PLUGIN_NAME,
        )
        for entry in entries
    ]
    shares.sort(key=lambda share: (share.is_fallback, -share.count, share.name))
    return tuple(shares)


def progress_view(snapshot: JobSnapshot) -> dict[str, Any]:
    """Return what a running crawl's page shows.

    Every count leaves here as a string a template can print unchanged. That is
    what lets the browser's live update write values into the page without
    formatting anything: the numbers it receives are the ones the server would
    have rendered.
    """
    return {
        "job_id": snapshot.job_id,
        "seed_url": snapshot.seed_url,
        "state": str(snapshot.state),
        "state_label": _state_label(snapshot),
        "state_tone": _state_tone(snapshot),
        "options": describe_options(snapshot.options),
        "max_pages": format_number(snapshot.options.max_pages),
        "pages_visited": format_number(snapshot.pages_visited),
        "pages_failed": format_number(snapshot.pages_failed),
        "pages_attempted": format_number(snapshot.pages_attempted),
        "links_found": format_number(snapshot.links_found),
        "latest_url": snapshot.latest_url,
        "elapsed": format_duration(snapshot.elapsed_seconds),
        "progress_percent": round(snapshot.progress * 100),
        "is_finished": snapshot.is_finished,
        "error": snapshot.error,
    }


def download_view(snapshot: DownloadSnapshot) -> dict[str, Any]:
    """Return what a download's page shows.

    Every value leaves here as a string a template prints unchanged, for the
    same reason :func:`progress_view` does: the live update writes the server's
    own formatting into the page rather than inventing its own.

    ``progress_percent`` is ``None`` when nothing stated a total. A bar that
    sits at zero for two minutes claims progress it cannot see, so the page
    shows an indeterminate one and says how much has arrived instead.
    """
    progress = snapshot.progress
    fraction = progress.fraction
    return {
        "download_id": snapshot.download_id,
        "url": snapshot.url,
        "label": snapshot.label,
        "status": str(snapshot.status),
        "state_label": STATUS_LABELS[snapshot.status],
        "state_tone": STATUS_TONES[snapshot.status],
        "bytes_written": format_size(progress.bytes_written),
        "total_bytes": None if progress.total_bytes is None else format_size(progress.total_bytes),
        "transferred": _transferred(progress.bytes_written, progress.total_bytes),
        "progress_percent": None if fraction is None else round(fraction * 100),
        "has_total": fraction is not None,
        "files_total": format_number(progress.files_total),
        "files_finished": format_number(progress.files_finished),
        "has_many_files": progress.files_total > 1,
        "elapsed": format_duration(snapshot.elapsed_seconds),
        "rate": _rate(progress.bytes_written, snapshot.elapsed_seconds),
        "remaining": _remaining(progress, snapshot),
        "is_finished": snapshot.is_finished,
        "succeeded": snapshot.summary is not None and snapshot.summary.succeeded,
        "reason": snapshot.reason,
        "error": snapshot.error,
        "path": None if snapshot.path is None else snapshot.path.as_posix(),
        # Straight to the file rather than to a list to search through. Absent
        # when the request turned out to hold several files, which is when the
        # library itself is the right place to land.
        "item_url": _item_url(snapshot.summary),
    }


MINIMUM_TIMED_SECONDS = 0.5
"""How long a transfer must have run before its speed means anything.

Two chunks in the first fifty milliseconds divide out to a rate no line is going
to sustain, and a page that opened by claiming 400 MB/s would be lying twice —
once about the speed and once about the time remaining.
"""


def _rate(written: int, elapsed: float) -> str | None:
    """Return how fast a transfer is moving, or ``None`` while that is guesswork."""
    if written <= 0 or elapsed < MINIMUM_TIMED_SECONDS:
        return None
    return f"{format_size(int(written / elapsed))}/s"


def _remaining(progress: DownloadProgress, snapshot: DownloadSnapshot) -> str | None:
    """Return how much longer this will take, when that can be said at all.

    Needs three things nobody is owed: a total, a rate, and a transfer that has
    not finished. The estimate is the crudest possible — bytes left over bytes
    per second so far — and is offered as an estimate rather than dressed up,
    because a download over a link nobody controls is not predictable.
    """
    total = progress.total_bytes
    written = progress.bytes_written
    if snapshot.is_finished or total is None or written <= 0:
        return None
    if snapshot.elapsed_seconds < MINIMUM_TIMED_SECONDS or written >= total:
        return None
    return format_duration((total - written) / (written / snapshot.elapsed_seconds))


def _can_display(payload: StoredPayload | None) -> bool:
    """Return whether there is a file here that a browser may be shown."""
    return payload is not None and payload.media.can_display


def _item_url(summary: DownloadSummary | None) -> str | None:
    """Return the library page of the one file a download fetched, if it was one."""
    if summary is None or summary.directory is None or summary.key is None:
        return None
    return f"/library/{summary.directory}/{summary.key}"


def library_view(page: LibraryPage) -> dict[str, Any]:
    """Return what the library page shows, links included.

    The sort links and the paging links are built here rather than in the
    template, because each of them is *this* query with one thing changed — and
    a template assembling query strings is a template deciding something.
    """
    query = page.query
    return {
        "rows": tuple(_library_row(item) for item in page.items),
        "columns": tuple(_column(label, sort, query) for label, sort in COLUMNS),
        "total": format_number(page.total),
        "stored": format_number(page.stored),
        "shown": f"{format_number(page.first)}–{format_number(page.last)}",
        "has_rows": bool(page.items),
        "is_filtered": query.is_filtered,
        "search": query.search,
        "provider": query.provider or "",
        "status": "" if query.status is None else str(query.status),
        # Carried through the filter form as hidden fields, so searching keeps
        # the order you had chosen instead of silently resetting it.
        "sort_value": str(query.sort),
        "direction": "desc" if query.descending else "asc",
        "providers": page.providers,
        "statuses": tuple(
            {"value": str(status), "label": STATUS_LABELS[status]} for status in page.statuses
        ),
        "page": format_number(page.page),
        "pages": format_number(page.pages),
        "previous_url": _library_url(query, page=page.page - 1) if page.has_previous else None,
        "next_url": _library_url(query, page=page.page + 1) if page.has_next else None,
        "reset_url": _library_url(LibraryQuery()),
    }


def item_view(item: LibraryItem, payload: StoredPayload | None) -> dict[str, Any]:
    """Return what one stored file's page shows.

    *payload* is what the service found on disk, and is ``None`` for two very
    different situations that the page has to tell apart: a download that failed
    and never wrote a file, and a record claiming a file that has since been
    deleted or moved. The first is a reason; the second is a repair.
    """
    base = f"/library/{item.directory}/{item.key}"
    return {
        "name": item.name,
        "provider": item.provider,
        "directory": item.directory,
        "key": item.key,
        "status": str(item.status),
        "state_label": STATUS_LABELS[item.status],
        "state_tone": STATUS_TONES[item.status],
        "size": format_size(item.size),
        "filename": item.filename,
        "downloaded_at": (
            None if item.downloaded_at is None else format_timestamp(item.downloaded_at)
        ),
        "discovered_at": (
            None if item.discovered_at is None else format_timestamp(item.discovered_at)
        ),
        "source_url": item.source_url,
        "path": None if item.path is None else str(item.path),
        "checksum": item.checksum,
        "attempts": format_number(item.attempts),
        "error": item.error,
        "library_url": "/library",
        "file_url": f"{base}/file" if payload is not None else None,
        "is_stored": payload is not None,
        # How, and whether, the page embeds the file itself. `display` is the
        # element to use rather than a type to branch on, so the template asks
        # no questions about media at all.
        "view_url": f"{base}/view" if _can_display(payload) else None,
        "display": None if payload is None else str(payload.media.display),
        "view_reason": None if payload is None else payload.media.reason,
        # Whether the frame showing it needs the `sandbox` attribute. True for
        # the types that could execute script; false for a PDF, which Chrome
        # refuses to render inside a sandboxed frame at all, and which cannot
        # reach our origin anyway.
        "sandboxed": payload is not None and payload.media.is_script_capable,
        # The record says there is a file and there is not: worth its own
        # sentence, because the answer is to download it again rather than to
        # wonder what the page means.
        "payload_missing": payload is None and item.is_stored,
    }


COLUMNS: tuple[tuple[str, LibrarySort], ...] = (
    ("Provider", LibrarySort.PROVIDER),
    ("Name", LibrarySort.NAME),
    ("Size", LibrarySort.SIZE),
    ("Downloaded", LibrarySort.DOWNLOADED),
    ("Status", LibrarySort.STATUS),
)
"""The sortable columns, in the order they are read."""

SORT_MARKS = {True: "▾", False: "▴"}
"""What marks the column a listing is ordered by, and which way."""


def _column(label: str, sort: LibrarySort, query: LibraryQuery) -> dict[str, Any]:
    """Return one column heading, and the link that reorders by it.

    Clicking the active column reverses it; clicking another one starts at the
    direction that column is most often wanted in — largest file and newest
    download first, names from A. Guessing right saves a second click and
    guessing wrong costs one, so the guess is worth making.
    """
    active = query.sort is sort
    descending = not query.descending if active else sort in _DESCENDING_FIRST
    return {
        "label": label,
        "url": _library_url(query, sort=sort, descending=descending, page=1),
        "active": active,
        "mark": SORT_MARKS[query.descending] if active else "",
    }


_DESCENDING_FIRST = frozenset({LibrarySort.SIZE, LibrarySort.DOWNLOADED})
"""Columns whose first click means "biggest first" rather than "smallest"."""


def _library_url(query: LibraryQuery, **changes: Any) -> str:
    """Return the library URL for *query* with *changes* applied.

    Only what differs from the default is written into the query string, so an
    unfiltered listing is plain ``/library`` and a bookmarked one carries exactly
    what it needs.
    """
    values = {
        "q": changes.get("search", query.search),
        "provider": changes.get("provider", query.provider) or "",
        "status": _status_value(changes.get("status", query.status)),
        "sort": str(changes.get("sort", query.sort)),
        "dir": "desc" if changes.get("descending", query.descending) else "asc",
        "page": str(changes.get("page", query.page)),
    }
    default = LibraryQuery()
    if values["sort"] == str(default.sort) and values["dir"] == "desc":
        del values["sort"], values["dir"]
    if values.get("page") == "1":
        del values["page"]
    written = {name: value for name, value in values.items() if value}
    return f"/library?{urlencode(written)}" if written else "/library"


def _status_value(status: DownloadStatus | None) -> str:
    """Return a status as a query string writes it, or nothing."""
    return "" if status is None else str(status)


def _library_row(item: LibraryItem) -> dict[str, Any]:
    """Return one stored resource as a table row."""
    return {
        "provider": item.provider,
        "name": item.name,
        "size": format_size(item.size),
        "downloaded_at": (
            "—" if item.downloaded_at is None else format_timestamp(item.downloaded_at)
        ),
        # Native separators, unlike the configured paths on the settings page.
        # A configured value is written into a TOML file, which spells them one
        # way on every platform; this is a location somebody pastes into their
        # file manager, and there it has to be spelled the way the platform does.
        "path": "—" if item.path is None else str(item.path),
        "source_url": item.source_url,
        "status": str(item.status),
        "state_label": STATUS_LABELS[item.status],
        "state_tone": STATUS_TONES[item.status],
        "url": f"/library/{item.directory}/{item.key}",
    }


def _transferred(written: int, total: int | None) -> str:
    """Return the byte counter under the bar, in the one form that reads well.

    "1.3 MB of 2.8 MB" while a total is known, and just what has arrived while
    it is not — never "1.3 MB of unknown", which is a sentence nobody wants.
    """
    if total is None:
        return format_size(written)
    return f"{format_size(written)} of {format_size(total)}"


def report_view(report: CrawlReport) -> dict[str, Any]:
    """Return what a finished crawl's page shows."""
    statistics = report.statistics
    summary = report.summary
    return {
        "job_id": report.session.session_id,
        "seed_url": report.seed_url,
        "state": str(report.state),
        "state_label": STATE_LABELS[report.state],
        "state_tone": STATE_TONES[report.state],
        "options": describe_options(report.session.options),
        "elapsed": format_duration(statistics.elapsed_seconds),
        "finished_at": format_timestamp(report.finished_at),
        "pages_visited": format_number(statistics.pages_visited),
        "pages_failed": format_number(statistics.pages_failed),
        "pages_attempted": format_number(statistics.pages_attempted),
        "pages_skipped": format_number(statistics.pages_skipped),
        "max_depth_reached": statistics.max_depth_reached,
        "frontier_remaining": format_number(statistics.frontier_remaining),
        "requests_without_a_page": format_number(statistics.requests_without_a_page),
        "links_found": format_number(summary.total_urls),
        "unique_urls": format_number(summary.unique_urls),
        "duplicates_removed": format_number(summary.duplicates_removed),
        "unresolved_urls": format_number(summary.statistics.unresolved_urls),
        "has_failures": statistics.pages_failed > 0,
        "hit_the_page_limit": report.state is CrawlState.PAGE_LIMIT,
        "left_in_frontier": statistics.frontier_remaining > 0,
        # Worth a line only when it differs from the two visible counters: it
        # is what explains a page ceiling arriving sooner than they suggest.
        # A boolean, so no template ends up comparing a formatted number.
        "had_answers_that_were_not_pages": statistics.requests_without_a_page > 0,
        "skips": _skip_rows(statistics.skips_by_reason),
        "link_kinds": _kind_rows(statistics.links_by_kind),
        "plugins": plugin_shares(summary.plugin_usage),
        "json_url": f"/crawls/{report.session.session_id}.json",
    }


def page_table(report: CrawlReport, *, limit: int = MAX_LISTED_PAGES) -> dict[str, Any]:
    """Return the page table, and an honest count of what it left out."""
    total = len(report.pages)
    return {
        "rows": page_rows(report, limit=limit),
        "total": format_number(total),
        "hidden": format_number(max(0, total - limit)),
        "has_hidden": total > limit,
    }


def link_table(
    urls: Iterable[StoredUrl],
    *,
    discovered: int,
    limit: int = MAX_LISTED_LINKS,
    downloadable: Container[str] = (),
) -> dict[str, Any]:
    """Return the link table for the URLs one crawl recorded.

    *discovered* is what the report counted, which is not always what the
    database holds: a crawl run without persistence records nothing at all. The
    two numbers are kept apart so the page can say "not recorded" rather than
    showing an empty table that reads as "nothing found".

    *downloadable* names the URLs some provider here could fetch, which is what
    decides whether a row offers a Download button. Deciding it once for the
    whole table rather than per row is why it arrives as a set: the answer comes
    from a plugin and a declared capability, and asking it two hundred times
    would be two hundred identical resolutions.
    """
    recorded = tuple(urls)
    total = len(recorded)
    rows = link_rows(recorded, limit=limit, downloadable=downloadable)
    return {
        "rows": rows,
        "total": format_number(total),
        "hidden": format_number(max(0, total - limit)),
        "has_hidden": total > limit,
        "discovered": format_number(discovered),
        "was_recorded": total > 0 or discovered == 0,
        # A column of empty cells is worse than no column: the table only grows
        # an action when at least one row actually has one.
        "has_downloads": any(row["can_download"] for row in rows),
    }


def crawl_rows(
    crawls: Iterable[StoredCrawl], *, live: Container[str] = ()
) -> tuple[dict[str, Any], ...]:
    """Return one row per recorded crawl, for the lists that show history.

    Reads from the stored summary rather than from the job registry, because
    the registry is a live view that dies with the process and this is the part
    that should survive a restart.

    *live* names the crawls this process is actually running. A row is written
    as ``running`` when the database says unfinished *and* this process agrees;
    a row the database left unfinished that nobody is running is not running,
    it was abandoned when whatever started it stopped. Saying "running" about a
    crawl from last week would be a page waiting for something that will never
    arrive.
    """
    return tuple(_crawl_row(crawl, is_live=crawl.session_id in live) for crawl in crawls)


def _crawl_row(crawl: StoredCrawl, *, is_live: bool) -> dict[str, Any]:
    """Return one recorded crawl as a table row."""
    options = CrawlOptions(
        max_depth=crawl.max_depth,
        max_pages=max(1, crawl.max_pages),
        same_domain=crawl.same_domain,
        include_subdomains=crawl.include_subdomains,
    )
    unfinished = crawl.finished_at is None
    abandoned = unfinished and not is_live
    return {
        "job_id": crawl.session_id,
        "url": f"/crawls/{crawl.session_id}",
        "seed_url": crawl.seed_url,
        "state": str(crawl.state),
        "state_label": ABANDONED_LABEL if abandoned else STATE_LABELS[crawl.state],
        "state_tone": "bad" if abandoned else STATE_TONES[crawl.state],
        "options": describe_options(options),
        "started_at": format_timestamp(crawl.started_at),
        "finished_at": "—" if crawl.finished_at is None else format_timestamp(crawl.finished_at),
        "is_running": unfinished and is_live,
        "was_abandoned": abandoned,
        "pages_visited": format_number(crawl.pages_visited),
        "pages_failed": format_number(crawl.pages_failed),
        "has_failures": crawl.pages_failed > 0,
        "links_found": format_number(crawl.links_discovered),
        "elapsed": format_duration(crawl.elapsed_seconds),
    }


def stored_view(crawl: StoredCrawl, *, is_live: bool = False) -> dict[str, Any]:
    """Return what the page of a crawl this server did not run shows.

    Less than :func:`report_view`, and the difference is not an oversight: the
    database keeps a summary and the URLs, never the per-page outcomes or the
    reasons individual URLs were turned away. The page states that rather than
    showing empty tables that would read as "none".
    """
    row = _crawl_row(crawl, is_live=is_live)
    return {
        **row,
        "pages_attempted": format_number(crawl.pages_attempted),
        "pages_skipped": format_number(crawl.pages_skipped),
        "max_depth_reached": crawl.max_depth_reached,
        "frontier_remaining": format_number(crawl.frontier_remaining),
        "left_in_frontier": crawl.frontier_remaining > 0,
    }


def settings_view(settings: Settings) -> tuple[dict[str, Any], ...]:
    """Return the effective configuration, grouped the way it is thought about.

    Every value as a string. A settings page that formatted numbers one way and
    the TOML beside it another would invite exactly the confusion it exists to
    remove.
    """
    return (
        {
            "heading": "Identity",
            "rows": (_setting("user_agent", settings.user_agent, "Sent with every request."),),
        },
        {
            "heading": "Storage",
            "rows": (
                _setting(
                    "database_path",
                    settings.database_path.as_posix(),
                    "Where crawls and discovered URLs are recorded.",
                ),
                _setting(
                    "library_path",
                    settings.library_path.as_posix(),
                    "Where downloads are stored, one directory per resource.",
                ),
                _setting(
                    "max_view_bytes",
                    format_bytes(settings.max_view_bytes),
                    "Largest stored file the browser is offered inline.",
                ),
                _setting("log_level", settings.log_level, ""),
                _setting("max_entries", format_number(settings.max_entries), ""),
            ),
        },
        {
            "heading": "Crawl defaults",
            "rows": (
                _setting(
                    "crawl_depth",
                    str(settings.crawl_depth),
                    "How far a crawl follows links unless told otherwise.",
                ),
                _setting(
                    "crawl_max_pages",
                    format_number(settings.crawl_max_pages),
                    "The ceiling every crawl is measured against.",
                ),
                _setting(
                    "crawl_same_domain",
                    _toml_bool(settings.crawl_same_domain),
                    "Off by default, so a share link to another host still works.",
                ),
            ),
        },
        {
            "heading": "Network",
            "rows": (
                _setting("network_timeout", f"{settings.network_timeout:g} s", ""),
                _setting("network_retries", str(settings.network_retries), ""),
                _setting(
                    "max_redirects",
                    str(settings.max_redirects),
                    "Hops one fetch may follow before the chain is called a loop.",
                ),
                _setting(
                    "max_page_bytes",
                    format_bytes(settings.max_page_bytes),
                    "Upper bound on one page, before and after decompression.",
                ),
                _setting(
                    "max_links",
                    format_number(settings.max_links),
                    "How many links one page may contribute.",
                ),
            ),
        },
    )


def format_bytes(value: int) -> str:
    """Return a byte count the way the person who configured it wrote it."""
    for unit, size in (("MiB", 1024**2), ("KiB", 1024)):
        if value >= size and value % size == 0:
            return f"{value // size} {unit}"
    return f"{format_number(value)} bytes"


def _setting(name: str, value: str, explanation: str) -> dict[str, Any]:
    """Return one configured value as a row."""
    return {"name": name, "value": value, "explanation": explanation}


def _toml_bool(value: bool) -> str:
    """Return a boolean as the configuration file spells it."""
    return "true" if value else "false"


def page_rows(report: CrawlReport, *, limit: int | None = None) -> tuple[dict[str, Any], ...]:
    """Return one row per page the crawl reached, in the order it reached them."""
    pages = report.pages if limit is None else report.pages[:limit]
    return tuple(_page_row(page) for page in pages)


def _page_row(page: PageOutcome) -> dict[str, Any]:
    """Return one page as a table row."""
    return {
        "url": page.url,
        "final_url": page.final_url,
        "was_redirected": page.was_redirected,
        "depth": page.depth,
        "status": page.status,
        "status_label": "err" if page.status is None else str(page.status),
        "title": page.title,
        "canonical_url": page.canonical_url,
        "link_count": format_number(page.link_count),
        "error": page.error,
        "succeeded": page.succeeded,
    }


def link_rows(
    urls: Iterable[StoredUrl], *, limit: int | None = None, downloadable: Container[str] = ()
) -> tuple[dict[str, Any], ...]:
    """Return one row per recorded URL, the interesting plugins first.

    Same ordering as :func:`plugin_shares`, for the same reason. Discovery
    order would be the honest default, but a page of share links produces
    thousands of generic URLs and a handful of Mega ones, and a table cut off
    at two hundred rows would then contain none of the links this project
    exists to find. Within each group the discovery order is kept.
    """
    ordered = sorted(enumerate(urls), key=lambda entry: (_link_priority(entry[1]), entry[0]))
    chosen = ordered if limit is None else ordered[:limit]
    return tuple(_link_row(stored, downloadable=downloadable) for _, stored in chosen)


def _link_row(stored: StoredUrl, *, downloadable: Container[str] = ()) -> dict[str, Any]:
    """Return one recorded URL as a table row."""
    record = stored.record
    return {
        "url": record.normalized_url,
        "raw_url": record.raw_url,
        "was_normalized": record.raw_url != record.normalized_url,
        "source_url": record.source_url,
        "plugin": stored.plugin_name or "unresolved",
        "category": stored.category or "—",
        # True when a host-specific plugin claimed it rather than the fallback,
        # which is what the table gives its one piece of emphasis to.
        "is_notable": _link_priority(stored) == 0,
        # A link this installation could actually fetch. Not the same question
        # as "is it notable": a host-specific plugin can classify a link whose
        # provider cannot transfer anything.
        "can_download": record.normalized_url in downloadable,
    }


def _link_priority(stored: StoredUrl) -> int:
    """Return which group a recorded URL sorts into: host, generic, unresolved."""
    if stored.plugin_name is None:
        return 2
    return 1 if stored.plugin_name == GENERIC_PLUGIN_NAME else 0


def _skip_rows(skips: Iterable[tuple[SkipReason, int]]) -> tuple[dict[str, Any], ...]:
    """Return why URLs were turned away, most frequent first."""
    return tuple({"reason": str(reason), "count": format_number(count)} for reason, count in skips)


def _kind_rows(kinds: Iterable[tuple[LinkKind, int]]) -> tuple[dict[str, Any], ...]:
    """Return how links were written, in the order the report lists them."""
    return tuple(
        {"kind": KIND_LABELS[kind], "count": format_number(count)} for kind, count in kinds
    )


def _state_label(snapshot: JobSnapshot) -> str:
    """Return the badge text for a snapshot, error included."""
    if snapshot.error is not None:
        return "failed"
    return STATE_LABELS[snapshot.state]


def _state_tone(snapshot: JobSnapshot) -> str:
    """Return the style class for a snapshot, error included."""
    if snapshot.error is not None:
        return "bad"
    return STATE_TONES[snapshot.state]
