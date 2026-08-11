"""One crawl report, as data any client can hand out.

The shape below started life inside :mod:`maxicrawler.cli.crawling`, which was
right for as long as the terminal was the only thing that served it. It is not
right now: the web interface answers ``/crawls/{id}.json`` with the same
document, and two clients each keeping their own idea of what a crawl looks
like is precisely the duplication this package exists to prevent.

What stays with the client is the *formatting*. The terminal indents its JSON
for a person reading a scrollback; an HTTP response does not. That is a
rendering decision, so it lives where the rendering does — here there is only
the document.

Nothing here reads :class:`~maxicrawler.web.session.RequestContext`. It is
reachable from a report by traversal, and this is one of the two places besides
the database adapter where a credential could escape, so a test asserts the
omission rather than trusting this sentence.
"""

from typing import Any

from maxicrawler.web.report import CrawlReport, PageOutcome


def crawl_document(report: CrawlReport) -> dict[str, Any]:
    """Return *report* as a JSON-ready mapping.

    It states the crawl, every page it reached and the counters — and nothing
    about how the requests were made.
    """
    statistics = report.statistics
    return {
        "session_id": report.session.session_id,
        "seed_url": report.seed_url,
        "state": str(report.state),
        "started_at": report.session.started_at.isoformat(),
        "finished_at": report.finished_at.isoformat(),
        "options": {
            "max_depth": report.session.options.max_depth,
            "max_pages": report.session.options.max_pages,
            "same_domain": report.session.options.same_domain,
            "include_subdomains": report.session.options.include_subdomains,
            "below_seed": report.session.options.below_seed,
            "scope": str(report.session.options.scope),
        },
        "statistics": {
            "pages_visited": statistics.pages_visited,
            "pages_failed": statistics.pages_failed,
            "pages_attempted": statistics.pages_attempted,
            "pages_skipped": statistics.pages_skipped,
            "skips_by_reason": {str(reason): count for reason, count in statistics.skips_by_reason},
            "links_by_kind": {str(kind): count for kind, count in statistics.links_by_kind},
            "links_discovered": report.links_discovered,
            "max_depth_reached": statistics.max_depth_reached,
            "frontier_remaining": statistics.frontier_remaining,
            "elapsed_seconds": round(statistics.elapsed_seconds, 3),
        },
        "pages": [page_document(page) for page in report.pages],
        "discovery": {
            "documents_processed": report.summary.documents_processed,
            "total_urls": report.summary.total_urls,
            "unique_urls": report.summary.unique_urls,
            "duplicates_removed": report.summary.duplicates_removed,
            "unresolved_urls": report.summary.statistics.unresolved_urls,
            "plugin_usage": [
                {"name": usage.name, "count": usage.count} for usage in report.summary.plugin_usage
            ],
        },
    }


def page_document(page: PageOutcome) -> dict[str, Any]:
    """Return one page outcome as a JSON-ready mapping."""
    return {
        "url": page.url,
        "final_url": page.final_url,
        "depth": page.depth,
        "status": page.status,
        "discovered_from": page.discovered_from,
        "title": page.title,
        "canonical_url": page.canonical_url,
        "link_count": page.link_count,
        "error": page.error,
    }
