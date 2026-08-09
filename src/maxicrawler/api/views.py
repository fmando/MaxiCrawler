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

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from maxicrawler.api.jobs import JobSnapshot
from maxicrawler.crawler import PluginUsage
from maxicrawler.database import StoredCrawl, StoredUrl
from maxicrawler.plugins.generic import GENERIC_PLUGIN_NAME
from maxicrawler.web.models import LinkKind
from maxicrawler.web.report import CrawlReport, PageOutcome, SkipReason
from maxicrawler.web.session import CrawlOptions, CrawlState

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
    urls: Iterable[StoredUrl], *, discovered: int, limit: int = MAX_LISTED_LINKS
) -> dict[str, Any]:
    """Return the link table for the URLs one crawl recorded.

    *discovered* is what the report counted, which is not always what the
    database holds: a crawl run without persistence records nothing at all. The
    two numbers are kept apart so the page can say "not recorded" rather than
    showing an empty table that reads as "nothing found".
    """
    recorded = tuple(urls)
    total = len(recorded)
    return {
        "rows": link_rows(recorded, limit=limit),
        "total": format_number(total),
        "hidden": format_number(max(0, total - limit)),
        "has_hidden": total > limit,
        "discovered": format_number(discovered),
        "was_recorded": total > 0 or discovered == 0,
    }


def crawl_rows(crawls: Iterable[StoredCrawl]) -> tuple[dict[str, Any], ...]:
    """Return one row per recorded crawl, for the lists that show history.

    Reads from the stored summary rather than from the job registry, because
    the registry is a live view that dies with the process and this is the part
    that should survive a restart.
    """
    return tuple(_crawl_row(crawl) for crawl in crawls)


def _crawl_row(crawl: StoredCrawl) -> dict[str, Any]:
    """Return one recorded crawl as a table row."""
    options = CrawlOptions(
        max_depth=crawl.max_depth,
        max_pages=max(1, crawl.max_pages),
        same_domain=crawl.same_domain,
        include_subdomains=crawl.include_subdomains,
    )
    return {
        "job_id": crawl.session_id,
        "url": f"/crawls/{crawl.session_id}",
        "seed_url": crawl.seed_url,
        "state": str(crawl.state),
        "state_label": STATE_LABELS[crawl.state],
        "state_tone": STATE_TONES[crawl.state],
        "options": describe_options(options),
        "started_at": format_timestamp(crawl.started_at),
        "is_running": crawl.finished_at is None,
        "pages_visited": format_number(crawl.pages_visited),
        "pages_failed": format_number(crawl.pages_failed),
        "has_failures": crawl.pages_failed > 0,
        "links_found": format_number(crawl.links_discovered),
        "elapsed": format_duration(crawl.elapsed_seconds),
    }


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


def link_rows(urls: Iterable[StoredUrl], *, limit: int | None = None) -> tuple[dict[str, Any], ...]:
    """Return one row per recorded URL, the interesting plugins first.

    Same ordering as :func:`plugin_shares`, for the same reason. Discovery
    order would be the honest default, but a page of share links produces
    thousands of generic URLs and a handful of Mega ones, and a table cut off
    at two hundred rows would then contain none of the links this project
    exists to find. Within each group the discovery order is kept.
    """
    ordered = sorted(enumerate(urls), key=lambda entry: (_link_priority(entry[1]), entry[0]))
    chosen = ordered if limit is None else ordered[:limit]
    return tuple(_link_row(stored) for _, stored in chosen)


def _link_row(stored: StoredUrl) -> dict[str, Any]:
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
