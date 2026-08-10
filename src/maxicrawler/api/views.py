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

from collections.abc import Container, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
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
    LinkItem,
    LinkPage,
    LinkQuery,
    LinkSort,
    PageQuery,
    PageSlice,
    PageState,
    StoredPayload,
    TargetKind,
)
from maxicrawler.config import Settings
from maxicrawler.crawler import PluginUsage
from maxicrawler.database import StoredCrawl
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
    DownloadStatus.CANCELLED: "stopped",
}
"""A skipped download is not a lesser success; it is one that needed no bytes.

A stopped one is not a failure either. The person reading that word is the
person who clicked the button, and calling their decision an error is how an
interface teaches somebody to distrust it.
"""

STATUS_TONES: dict[DownloadStatus, str] = {
    DownloadStatus.PENDING: "idle",
    DownloadStatus.RUNNING: "busy",
    DownloadStatus.COMPLETED: "good",
    DownloadStatus.SKIPPED: "good",
    DownloadStatus.FAILED: "bad",
    DownloadStatus.CANCELLED: "idle",
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
    """Return the one line that says what a crawl was told to do.

    robots.txt is stated either way rather than only when it was ignored.
    "Did this crawl obey robots.txt" is a question asked of a run that finished
    months ago, and silence would answer it only for somebody who already knew
    the default — which is exactly the person who would not be asking.
    """
    return (
        f"depth {options.max_depth} · {describe_scope(options)} · "
        f"max {format_number(options.max_pages)} pages · "
        f"robots.txt {'obeyed' if options.respect_robots else 'ignored'}"
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


PAGE_STATE_LABELS: dict[PageState, str] = {
    PageState.SUCCEEDED: "read",
    PageState.FAILED: "failed",
    PageState.REDIRECTED: "redirected",
}
"""What each state of a crawled page is called on a page.

"read" rather than "succeeded", because the counter above the table has said
"Pages read" since the first version of this report and two words for one
number is how a reader starts wondering whether they are two numbers.
"""

PAGE_PARAMS = frozenset({"pq", "pstate", "ppage"})
"""Which query parameters the page table owns.

Both tables live on one URL, so each has to know which parameters are not its
own — and carry them through untouched. Without that, filtering the pages would
silently throw away the link filter you were looking at, which is the kind of
thing that teaches somebody to stop using the filters.
"""


def page_view(
    slice: PageSlice, *, base: str, carry: Mapping[str, str] = MappingProxyType({})
) -> dict[str, Any]:
    """Return the page table, its filter, its chips and its paging.

    The same shape as :func:`link_view` and for the same reasons; what differs
    is that these records were never written down, so there is no order to
    choose and no column to hide. The order a crawl reached its pages in is the
    only one that means anything.
    """
    query = slice.query
    return {
        "rows": page_rows(slice.items),
        "total": format_number(slice.total),
        "recorded": format_number(slice.recorded),
        "has_rows": bool(slice.items),
        "has_any": slice.recorded > 0,
        "is_filtered": query.is_filtered,
        "action": f"{base}#pages",
        "search": query.search,
        "state": "" if query.state is None else str(query.state),
        "chips": tuple(
            {
                "label": PAGE_STATE_LABELS[state],
                "count": format_number(slice.counts.of(state)),
                "active": query.state is state,
                "tone": "bad" if state is PageState.FAILED else "",
                "url": _page_url(
                    base,
                    query,
                    carry,
                    state=None if query.state is state else state,
                    page=1,
                ),
            }
            for state in PageState
            if slice.counts.of(state)
        ),
        "carried": tuple({"name": name, "value": value} for name, value in sorted(carry.items())),
        "page": format_number(slice.page),
        "pages": format_number(slice.pages),
        "shown_range": f"{format_number(slice.first)}–{format_number(slice.last)}",
        "previous_url": (
            _page_url(base, query, carry, page=slice.page - 1) if slice.has_previous else None
        ),
        "next_url": _page_url(base, query, carry, page=slice.page + 1) if slice.has_next else None,
        "reset_url": _page_url(base, PageQuery(), carry),
    }


def _page_url(base: str, query: PageQuery, carry: Mapping[str, str], **changes: Any) -> str:
    """Return the report URL for the page table's *query* with *changes* applied.

    *carry* is every parameter this table does not own, written back unchanged.
    """
    state = changes.get("state", query.state)
    values = {
        "pq": changes.get("search", query.search),
        "pstate": "" if state is None else str(state),
        "ppage": str(changes.get("page", query.page)),
    }
    if values["ppage"] == "1":
        del values["ppage"]
    written = {**carry, **{name: value for name, value in values.items() if value}}
    return f"{base}?{urlencode(written)}#pages" if written else f"{base}#pages"


@dataclass(frozen=True, slots=True)
class LinkColumn:
    """One column of the link table, and whether it can be ordered by."""

    name: str
    label: str
    sort: LinkSort | None = None


LINK_COLUMNS: tuple[LinkColumn, ...] = (
    LinkColumn("plugin", "Plugin", LinkSort.PLUGIN),
    LinkColumn("category", "Category"),
    LinkColumn("target", "Type"),
    LinkColumn("url", "URL", LinkSort.URL),
    LinkColumn("source", "Found on", LinkSort.SOURCE),
)
"""The columns a reader can turn off, in the order they are read.

``category`` and ``target`` are not sortable, and deliberately have no ordering
of their own: both are short labels with a handful of values, and grouping by
them is what the facet chips already do in one click.
"""

REQUIRED_COLUMN = "url"
"""The one column that cannot be hidden.

A table of discovered URLs without the URLs is not a narrower view of anything.
"""

LINK_ORDERS: tuple[tuple[LinkSort, str], ...] = (
    (LinkSort.RELEVANCE, "Plugin relevance"),
    (LinkSort.DISCOVERED, "Discovery order"),
    (LinkSort.URL, "URL"),
    (LinkSort.PLUGIN, "Plugin name"),
    (LinkSort.SOURCE, "Page it was found on"),
)
"""Every order offered, including the two that are not columns.

"Plugin relevance" is the default and is named rather than left implicit: a
reader who notices that Mega links are on top deserves to be told that this is
a choice, and given the one that is not.
"""

TARGET_LABELS: dict[TargetKind, str] = {
    TargetKind.DOCUMENT: "documents",
    TargetKind.IMAGE: "images",
    TargetKind.ARCHIVE: "archives",
    TargetKind.VIDEO: "video",
    TargetKind.AUDIO: "audio",
    TargetKind.PAGE: "pages (.html)",
    TargetKind.UNKNOWN: "not stated",
}
"""What each target kind is called on a page.

"pages (.html)" says the extension because the filter means exactly that and
nothing wider, and "not stated" rather than "unknown" because the URL not
saying is a fact about the URL, not a gap in what we know.
"""

DOWNLOADABLE_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "any"),
    ("yes", "can be downloaded"),
    ("no", "cannot"),
)


LINK_PARAMS = frozenset(
    {"q", "plugin", "category", "target", "dl", "norm", "sort", "dir", "page", "hide"}
)
"""Which query parameters the link table owns; see :data:`PAGE_PARAMS`."""


def link_view(
    page: LinkPage,
    *,
    base: str,
    hidden: Container[str] = (),
    carry: Mapping[str, str] = MappingProxyType({}),
) -> dict[str, Any]:
    """Return the link table, its filters, its facets and its paging.

    Everything that decides *which* URLs these are was decided by
    :class:`~maxicrawler.app.discovery.DiscoveryService` before this was called.
    What is left is wording, formatting, and building the links — each of which
    is this query with one thing changed, which is exactly the kind of decision
    a template must not be making.

    *base* is the page the table lives on, because a report's URL contains the
    crawl it belongs to. Every link this builds ends at ``#links``, so choosing
    a filter puts you back at the table rather than at the top of a long page.
    """
    query = page.query
    rows = link_rows(page)
    shown = frozenset(
        column.name for column in LINK_COLUMNS if column.name not in hidden
    ) | frozenset({REQUIRED_COLUMN})
    return {
        "rows": rows,
        "shown": shown,
        "headers": tuple(
            _link_header(column, query, base=base, hidden=hidden, carry=carry)
            for column in LINK_COLUMNS
            if column.name in shown
        ),
        "toggles": tuple(
            _link_toggle(column, query, base=base, hidden=hidden, carry=carry)
            for column in LINK_COLUMNS
        ),
        "total": format_number(page.total),
        "recorded": format_number(page.recorded),
        "discovered": format_number(page.discovered),
        "was_recorded": page.was_recorded,
        "has_rows": bool(page.items),
        # Whether there is anything to filter at all, which is a different
        # question from whether this page has rows: a filter bar above "nothing
        # matches that" is useful, and one above a crawl that found nothing is
        # a control with no purpose.
        "has_any": page.recorded > 0,
        # A column of empty cells is worse than no column: the table only grows
        # an action when at least one row actually has one.
        "has_downloads": any(row["can_download"] for row in rows),
        "is_filtered": query.is_filtered,
        "action": f"{base}#links",
        # What the filter form shows as its current state.
        "search": query.search,
        "plugin": query.plugin or "",
        "category": query.category or "",
        "target": "" if query.target is None else str(query.target),
        "downloadable": _downloadable_value(query.downloadable),
        "normalized_only": query.normalized_only,
        "downloadable_choices": DOWNLOADABLE_CHOICES,
        "orders": tuple(
            {"value": str(sort), "label": label, "selected": query.sort is sort}
            for sort, label in LINK_ORDERS
        ),
        # Carried through the filter form as hidden fields, so searching keeps
        # the order and the columns you had chosen instead of resetting them.
        "direction": "desc" if query.descending else "asc",
        "hide_value": _hide_value(hidden),
        "facets": _link_facets(page, base=base, hidden=hidden, carry=carry),
        "carried": tuple({"name": name, "value": value} for name, value in sorted(carry.items())),
        "page": format_number(page.page),
        "pages": format_number(page.pages),
        "shown_range": f"{format_number(page.first)}–{format_number(page.last)}",
        "previous_url": (
            _link_url(base, query, hidden, carry, page=page.page - 1) if page.has_previous else None
        ),
        "next_url": (
            _link_url(base, query, hidden, carry, page=page.page + 1) if page.has_next else None
        ),
        "reset_url": _link_url(base, LinkQuery(), hidden, carry),
    }


def _downloadable_value(downloadable: bool | None) -> str:
    """Return the downloadable filter as the form spells it."""
    if downloadable is None:
        return ""
    return "yes" if downloadable else "no"


def _link_header(
    column: LinkColumn,
    query: LinkQuery,
    *,
    base: str,
    hidden: Container[str],
    carry: Mapping[str, str],
) -> dict[str, Any]:
    """Return one column heading, and the link that reorders by it.

    Clicking the active column reverses it; clicking another starts ascending,
    which for a URL and a plugin name is the direction anybody means. A column
    that cannot be ordered by is a heading and nothing more.
    """
    sort = column.sort
    if sort is None:
        return {
            "label": column.label,
            "name": column.name,
            "url": None,
            "active": False,
            "mark": "",
        }
    active = query.sort is sort
    descending = not query.descending if active else False
    return {
        "label": column.label,
        "name": column.name,
        "url": _link_url(base, query, hidden, carry, sort=sort, descending=descending, page=1),
        "active": active,
        "mark": SORT_MARKS[query.descending] if active else "",
    }


def _link_toggle(
    column: LinkColumn,
    query: LinkQuery,
    *,
    base: str,
    hidden: Container[str],
    carry: Mapping[str, str],
) -> dict[str, Any]:
    """Return the link that shows or hides one column.

    A link rather than a checkbox, so the whole control is the same mechanism as
    a facet chip: the state lives in the URL, the server renders the answer, and
    it works with scripting switched off. The one column that cannot be hidden
    is offered as a disabled entry rather than left out, because a control that
    silently lacks an entry reads as a bug.
    """
    is_shown = column.name not in hidden
    required = column.name == REQUIRED_COLUMN
    now_hidden = {name for name in _column_names() if name in hidden}
    if is_shown:
        now_hidden.add(column.name)
    else:
        now_hidden.discard(column.name)
    return {
        "label": column.label,
        "name": column.name,
        "shown": is_shown,
        "required": required,
        "url": None if required else _link_url(base, query, frozenset(now_hidden), carry),
    }


def _column_names() -> tuple[str, ...]:
    """Return every column name, which is what a hide list may contain."""
    return tuple(column.name for column in LINK_COLUMNS)


def _link_facets(
    page: LinkPage, *, base: str, hidden: Container[str], carry: Mapping[str, str]
) -> tuple[dict[str, Any], ...]:
    """Return the chip rows that filter by one value in one click.

    Counted over the whole crawl rather than over the matches, which is what the
    service already decided; what is added here is that the chip you are
    standing on links back to the unfiltered view, so a chip is a toggle rather
    than a one-way door.
    """
    query = page.query
    groups = (
        ("Plugin", page.plugins, "plugin", query.plugin, lambda value: value),
        (
            "Type",
            page.targets,
            "target",
            "" if query.target is None else str(query.target),
            lambda value: TARGET_LABELS[TargetKind(value)],
        ),
        ("Category", page.categories, "category", query.category, lambda value: value),
    )
    rows = []
    for heading, facets, name, active, label_of in groups:
        if not facets:
            continue
        rows.append(
            {
                "heading": heading,
                "chips": tuple(
                    {
                        "label": label_of(facet.value),
                        "count": format_number(facet.count),
                        "active": facet.value == active,
                        "url": _link_url(
                            base,
                            query,
                            hidden,
                            carry,
                            page=1,
                            **{name: None if facet.value == active else facet.value},
                        ),
                    }
                    for facet in facets
                ),
            }
        )
    return tuple(rows)


def _hide_value(hidden: Container[str]) -> str:
    """Return the hidden columns as the query string writes them."""
    return ",".join(name for name in _column_names() if name in hidden)


def _link_url(
    base: str,
    query: LinkQuery,
    hidden: Container[str],
    carry: Mapping[str, str] = MappingProxyType({}),
    **changes: Any,
) -> str:
    """Return the report URL for *query* with *changes* applied.

    Only what differs from the default is written, so an untouched report is
    plain ``/crawls/{id}`` and a filtered one carries exactly what it needs. The
    fragment is always there: every one of these links leads to the table, and a
    reader who clicks a filter should not have to scroll back down to it.
    """
    target = changes.get("target", query.target)
    downloadable = changes.get("downloadable", query.downloadable)
    values = {
        "q": changes.get("search", query.search),
        "plugin": changes.get("plugin", query.plugin) or "",
        "category": changes.get("category", query.category) or "",
        "target": "" if target is None else str(target),
        "dl": _downloadable_value(downloadable),
        "norm": "1" if changes.get("normalized_only", query.normalized_only) else "",
        "sort": str(changes.get("sort", query.sort)),
        "dir": "desc" if changes.get("descending", query.descending) else "asc",
        "page": str(changes.get("page", query.page)),
        "hide": _hide_value(hidden),
    }
    default = LinkQuery()
    if values["sort"] == str(default.sort):
        del values["sort"]
    if values["dir"] == "asc":
        del values["dir"]
    if values.get("page") == "1":
        del values["page"]
    written = {**carry, **{name: value for name, value in values.items() if value}}
    return f"{base}?{urlencode(written)}#links" if written else f"{base}#links"


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
        respect_robots=crawl.respect_robots,
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
        {
            "heading": "Responsible crawling",
            "rows": (
                _setting(
                    "respect_robots",
                    _toml_bool(settings.respect_robots),
                    "Obey each host's robots.txt. A URL it forbids is reported as skipped.",
                ),
                _setting(
                    "robots_user_agent",
                    settings.robots_user_agent or "(from user_agent)",
                    "Which product token robots.txt groups are matched against.",
                ),
                _setting(
                    "robots_timeout",
                    f"{settings.robots_timeout:g} s",
                    "How long to wait for a robots.txt before giving up on it.",
                ),
                _setting(
                    "robots_deny_on_error",
                    _toml_bool(settings.robots_deny_on_error),
                    "A host we could not reach is treated as forbidding everything.",
                ),
                _setting(
                    "crawl_delay",
                    f"{settings.crawl_delay:g} s",
                    "Waiting between requests to one host. Zero adds no delay of our own.",
                ),
                _setting(
                    "respect_crawl_delay",
                    _toml_bool(settings.respect_crawl_delay),
                    "Honour a Crawl-delay a host states for itself.",
                ),
                _setting(
                    "max_crawl_delay",
                    f"{settings.max_crawl_delay:g} s",
                    "The longest such delay obeyed, so one file cannot freeze a crawl.",
                ),
            ),
        },
        {
            "heading": "Private networks",
            "rows": (
                _setting(
                    "allow_private_networks",
                    _toml_bool(settings.allow_private_networks),
                    "Off, so a URL from a browser cannot reach this machine or this network.",
                ),
                _setting(
                    "private_network_allowlist",
                    ", ".join(settings.private_network_allowlist) or "(none)",
                    "Hosts, addresses or blocks exempt from that rule.",
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


def page_rows(pages: Iterable[PageOutcome]) -> tuple[dict[str, Any], ...]:
    """Return one row per page the crawl reached, in the order it reached them."""
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


def link_rows(page: LinkPage) -> tuple[dict[str, Any], ...]:
    """Return one row per URL on *page*, in the order the service put them in."""
    return tuple(_link_row(item, downloadable=page.downloadable) for item in page.items)


def _link_row(item: LinkItem, *, downloadable: Container[str] = ()) -> dict[str, Any]:
    """Return one recorded URL as a table row.

    ``plugin`` and ``category`` are where a URL nothing claimed gets its
    wording. The service leaves both as ``None``, because what to call an
    unanswered question is a decision for whoever is showing it — a terminal
    and a table legitimately word it differently.
    """
    return {
        "url": item.url,
        "raw_url": item.raw_url,
        "was_normalized": item.was_normalized,
        "source_url": item.source_url,
        "plugin": item.plugin or "unresolved",
        "category": item.category or "—",
        "target": TARGET_LABELS[item.target],
        # Whether the URL said anything at all, which is what decides emphasis:
        # "not stated" is true of most URLs and is not worth a reader's eye.
        "target_is_stated": item.target is not TargetKind.UNKNOWN,
        # True when a host-specific plugin claimed it rather than the fallback,
        # which is what the table gives its one piece of emphasis to.
        "is_notable": item.is_notable,
        # A link this installation could actually fetch. Not the same question
        # as "is it notable": a host-specific plugin can classify a link whose
        # provider cannot transfer anything.
        "can_download": item.url in downloadable,
    }


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
