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
from typing import Any

from maxicrawler.api.jobs import JobSnapshot
from maxicrawler.crawler import PluginUsage
from maxicrawler.plugins.generic import GENERIC_PLUGIN_NAME
from maxicrawler.web.models import LinkKind
from maxicrawler.web.report import CrawlReport, PageOutcome, SkipReason
from maxicrawler.web.session import CrawlOptions, CrawlState

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
    """Return what a running crawl's page shows."""
    return {
        "job_id": snapshot.job_id,
        "seed_url": snapshot.seed_url,
        "state": str(snapshot.state),
        "state_label": _state_label(snapshot),
        "state_tone": _state_tone(snapshot),
        "options": describe_options(snapshot.options),
        "max_pages": snapshot.options.max_pages,
        "pages_visited": snapshot.pages_visited,
        "pages_failed": snapshot.pages_failed,
        "pages_attempted": snapshot.pages_attempted,
        "links_found": snapshot.links_found,
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
        "finished_at": report.finished_at,
        "pages_visited": statistics.pages_visited,
        "pages_failed": statistics.pages_failed,
        "pages_attempted": statistics.pages_attempted,
        "pages_skipped": statistics.pages_skipped,
        "max_depth_reached": statistics.max_depth_reached,
        "frontier_remaining": statistics.frontier_remaining,
        "requests_without_a_page": statistics.requests_without_a_page,
        "links_found": summary.total_urls,
        "unique_urls": summary.unique_urls,
        "duplicates_removed": summary.duplicates_removed,
        "unresolved_urls": summary.statistics.unresolved_urls,
        "skips": _skip_rows(statistics.skips_by_reason),
        "link_kinds": _kind_rows(statistics.links_by_kind),
        "plugins": plugin_shares(summary.plugin_usage),
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
        "link_count": page.link_count,
        "error": page.error,
        "succeeded": page.succeeded,
    }


def _skip_rows(skips: Iterable[tuple[SkipReason, int]]) -> tuple[dict[str, Any], ...]:
    """Return why URLs were turned away, most frequent first."""
    return tuple({"reason": str(reason), "count": count} for reason, count in skips)


def _kind_rows(kinds: Iterable[tuple[LinkKind, int]]) -> tuple[dict[str, Any], ...]:
    """Return how links were written, in the order the report lists them."""
    return tuple({"kind": KIND_LABELS[kind], "count": count} for kind, count in kinds)


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
