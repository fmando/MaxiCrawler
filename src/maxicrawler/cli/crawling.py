"""Rendering of crawl reports for the terminal.

Both renderers are pure, so the wording and the JSON shape can be tested
without fetching anything. The discovery half of the report is
:func:`~maxicrawler.cli.summary.render_summary` verbatim, which is what keeps
``crawl`` and ``discover`` reading the same way.

One renderer serves a crawl of one page and a crawl of fifty. A single page
gets the detail Sprint 8 printed — where it went, what it is called; several
pages get one line each instead, because forty title lines are not a report.
Two renderers would have drifted within a sprint.

Nothing here reads :class:`~maxicrawler.web.session.RequestContext`. It is
reachable from a report by traversal, and this is one of the places besides the
database adapter where a credential could escape, so a test asserts the
omission rather than trusting this sentence.

The JSON document is not built here. It is
:func:`maxicrawler.app.crawl_document`, because the web interface serves the
same one, and only the indentation is a terminal decision.
"""

import json

from maxicrawler.app import crawl_document
from maxicrawler.cli.summary import render_summary
from maxicrawler.web import LinkKind
from maxicrawler.web.report import CrawlReport, PageOutcome
from maxicrawler.web.session import CrawlState

EXIT_CRAWLED = 0
"""The crawl ran to a natural end, or to a limit it was given."""

EXIT_FETCH_FAILED = 5
"""The seed could not be retrieved, so nothing was crawled."""

EXIT_NOT_A_PAGE = 6
"""The seed answered, but it was not HTML."""

EXIT_INTERRUPTED = 7
"""A stop was requested; what had been crawled is still reported."""

MAX_LISTED_PAGES = 25
"""How many pages a multi-page report lists before summarizing the rest."""

_KIND_LABELS: dict[LinkKind, str] = {
    LinkKind.ANCHOR: "anchor",
    LinkKind.IMAGE: "image",
    LinkKind.SCRIPT: "script",
    LinkKind.STYLESHEET: "stylesheet",
    LinkKind.FRAME: "frame",
    LinkKind.REDIRECT: "meta refresh",
    LinkKind.TEXT: "plain text",
}

_STATE_TEXT: dict[CrawlState, str] = {
    CrawlState.COMPLETED: "completed",
    CrawlState.PAGE_LIMIT: "stopped at the page limit",
    CrawlState.INTERRUPTED: "interrupted",
    CrawlState.PENDING: "never started",
    CrawlState.RUNNING: "still running",
}


def exit_code_for(report: CrawlReport) -> int:
    """Return the exit code for *report*.

    Hitting a configured limit is not a failure — it is the crawl doing what it
    was told. Only an interruption is reported differently, because the caller
    asked for it and may want to know it took effect.
    """
    if report.state is CrawlState.INTERRUPTED:
        return EXIT_INTERRUPTED
    return EXIT_CRAWLED


def render_crawl(report: CrawlReport) -> str:
    """Return the terminal report for *report*."""
    lines = [
        f"Crawl:     {report.seed_url}  ({_describe_options(report)})",
        f"Finished:  {_STATE_TEXT[report.state]} in {report.statistics.elapsed_seconds:.1f}s",
        "",
    ]
    lines.extend(_render_pages(report))
    lines.extend(_render_links(report))
    lines.extend(("", render_summary(report.summary)))
    return "\n".join(lines)


def render_crawl_json(report: CrawlReport) -> str:
    """Return *report* as a JSON document, indented for a scrollback.

    The document itself comes from :func:`maxicrawler.app.crawl_document`, which
    the web interface serves as well. Only the indentation is a terminal
    decision, and it is the only thing this function adds.
    """
    return json.dumps(crawl_document(report), indent=2, sort_keys=False)


def _describe_options(report: CrawlReport) -> str:
    """Return the one-line description of what the crawl was told to do."""
    options = report.session.options
    scope = "same domain" if options.same_domain else "any domain"
    if options.same_domain and options.include_subdomains:
        scope = "same domain and subdomains"
    return f"depth {options.max_depth}, {scope}, max {options.max_pages} pages"


def _render_pages(report: CrawlReport) -> list[str]:
    """Return the page section: detail for one page, a list for several."""
    if len(report.pages) == 1:
        return _render_single_page(report.pages[0])
    lines = [f"Pages visited: {report.statistics.pages_visited}"]
    for page in report.pages[:MAX_LISTED_PAGES]:
        lines.append(f"  {_page_line(page)}")
    remaining = len(report.pages) - MAX_LISTED_PAGES
    if remaining > 0:
        lines.append(f"  ... and {remaining} more")
    if report.statistics.pages_failed:
        lines.append(f"Pages failed: {report.statistics.pages_failed}")
    if report.statistics.requests_without_a_page:
        # Only when it differs, and then it explains the ceiling: these
        # requests cost a round trip without producing a page or a failure.
        lines.append(f"Pages attempted: {report.statistics.pages_attempted}")
    lines.extend(_render_skips(report))
    return lines


def _render_single_page(page: PageOutcome) -> list[str]:
    """Return the detail block for a crawl that visited exactly one page."""
    lines = [f"Fetched:   {page.url}"]
    if page.was_redirected:
        lines.append(f"Final URL: {page.final_url}")
    if page.status is not None:
        lines.append(f"Status:    {page.status}")
    if page.title:
        lines.append(f"Title:     {page.title}")
    if page.canonical_url:
        lines.append(f"Canonical: {page.canonical_url}")
    if page.error:
        lines.append(f"Error:     {page.error}")
    return lines


def _page_line(page: PageOutcome) -> str:
    """Return the one-line description of one visited page."""
    status = "err" if page.status is None else str(page.status)
    suffix = "  (failed)" if page.error else ""
    return f"{status:>3}  d{page.depth}  {page.url}{suffix}"


def _render_skips(report: CrawlReport) -> list[str]:
    """Return the skipped-URL section, naming why each one was turned away."""
    statistics = report.statistics
    if not statistics.pages_skipped:
        return []
    lines = [f"Pages skipped: {statistics.pages_skipped}"]
    lines.extend(f"  {reason}: {count}" for reason, count in statistics.skips_by_reason)
    return lines


def _render_links(report: CrawlReport) -> list[str]:
    """Return the link section, grouped by how each link was written."""
    lines = ["", f"Links found: {report.links_discovered}"]
    lines.extend(
        f"  {_KIND_LABELS[kind]}: {count}" for kind, count in report.statistics.links_by_kind
    )
    return lines
