"""Tests for the crawl document both clients hand out.

The point of this module existing at all is that there is one shape. So the
test that matters most is the one asserting the terminal's JSON *is* this
document, rather than something that currently happens to look like it.
"""

import json
from datetime import UTC, datetime

from maxicrawler.app import crawl_document, page_document
from maxicrawler.cli.crawling import render_crawl_json
from maxicrawler.crawler import DiscoverySummary, PluginUsage
from maxicrawler.domain import ScanSession, Statistics
from maxicrawler.web.models import LinkKind
from maxicrawler.web.report import CrawlReport, CrawlStatistics, PageOutcome, SkipReason
from maxicrawler.web.session import (
    CrawlOptions,
    CrawlSession,
    CrawlState,
    RequestContext,
)

STARTED = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def make_report(**kwargs: object) -> CrawlReport:
    """Return a finished crawl report."""
    session = CrawlSession(
        session_id="crawl-1",
        seed_url="https://example.test/",
        started_at=STARTED,
        options=CrawlOptions(max_depth=2, max_pages=50, same_domain=True),
        context=kwargs.pop("context", RequestContext(user_agent="MaxiCrawler/test")),  # type: ignore[arg-type]
    )
    values: dict[str, object] = {
        "session": session,
        "state": CrawlState.COMPLETED,
        "statistics": CrawlStatistics(
            pages_visited=4,
            pages_failed=1,
            pages_attempted=6,
            pages_skipped=12,
            skips_by_reason=((SkipReason.NOT_A_PAGE, 8), (SkipReason.ALREADY_SEEN, 4)),
            links_by_kind=((LinkKind.ANCHOR, 30), (LinkKind.IMAGE, 5)),
            max_depth_reached=2,
            frontier_remaining=3,
            elapsed_seconds=1.2345,
        ),
        "summary": DiscoverySummary(
            session=ScanSession("crawl-1", STARTED),
            statistics=Statistics(documents_processed=4, discovered_urls=21, duplicate_urls=14),
            plugin_usage=(PluginUsage("generic", 18), PluginUsage("mega", 3)),
        ),
        "pages": (
            PageOutcome(
                url="https://example.test/",
                final_url="https://example.test/",
                depth=0,
                status=200,
                title="Home",
                link_count=9,
            ),
        ),
        "finished_at": datetime(2026, 8, 9, 12, 5, tzinfo=UTC),
    }
    values.update(kwargs)
    return CrawlReport(**values)  # type: ignore[arg-type]


# --- the document ------------------------------------------------------------


def test_the_document_names_the_crawl() -> None:
    document = crawl_document(make_report())

    assert document["session_id"] == "crawl-1"
    assert document["seed_url"] == "https://example.test/"
    assert document["state"] == "completed"
    assert document["started_at"] == "2026-08-09T12:00:00+00:00"
    assert document["finished_at"] == "2026-08-09T12:05:00+00:00"


def test_the_document_states_what_the_crawl_was_told_to_do() -> None:
    assert crawl_document(make_report())["options"] == {
        "max_depth": 2,
        "max_pages": 50,
        "same_domain": True,
        "include_subdomains": False,
    }


def test_the_document_carries_every_counter() -> None:
    statistics = crawl_document(make_report())["statistics"]

    assert statistics["pages_visited"] == 4
    assert statistics["pages_failed"] == 1
    assert statistics["pages_attempted"] == 6
    assert statistics["pages_skipped"] == 12
    assert statistics["skips_by_reason"] == {"not a page link": 8, "already seen": 4}
    assert statistics["links_by_kind"] == {"anchor": 30, "image": 5}
    assert statistics["links_discovered"] == 35
    assert statistics["max_depth_reached"] == 2
    assert statistics["frontier_remaining"] == 3
    assert statistics["elapsed_seconds"] == 1.234


def test_the_document_carries_the_discovery_half() -> None:
    discovery = crawl_document(make_report())["discovery"]

    assert discovery["documents_processed"] == 4
    assert discovery["total_urls"] == 35
    assert discovery["unique_urls"] == 21
    assert discovery["duplicates_removed"] == 14
    assert discovery["plugin_usage"] == [
        {"name": "generic", "count": 18},
        {"name": "mega", "count": 3},
    ]


def test_one_page_becomes_one_entry() -> None:
    page = page_document(
        PageOutcome(
            url="https://example.test/old",
            final_url="https://example.test/new",
            depth=1,
            status=200,
            discovered_from="https://example.test/",
            title="New",
            canonical_url="https://example.test/new",
            link_count=3,
        )
    )

    assert page["url"] == "https://example.test/old"
    assert page["final_url"] == "https://example.test/new"
    assert page["discovered_from"] == "https://example.test/"
    assert page["link_count"] == 3
    assert page["error"] is None


def test_the_document_survives_json() -> None:
    """Nothing in it may need a custom encoder; a route hands it straight out."""
    restored = json.loads(json.dumps(crawl_document(make_report())))

    assert restored["statistics"]["pages_visited"] == 4


# --- what must not be in it --------------------------------------------------


def test_the_document_says_nothing_about_how_requests_were_made() -> None:
    """A credential added to the context later must not leak by traversal."""
    context = RequestContext(user_agent="MaxiCrawler/secret-agent")

    text = json.dumps(crawl_document(make_report(context=context)))

    assert "secret-agent" not in text
    assert "user_agent" not in text


# --- one shape, two clients --------------------------------------------------


def test_the_terminal_prints_this_very_document() -> None:
    """The reason this module exists. Two shapes would drift within a sprint."""
    report = make_report()

    assert json.loads(render_crawl_json(report)) == crawl_document(report)


def test_the_terminal_indents_and_the_document_does_not_care() -> None:
    """Indentation is a rendering decision, so it stays with the renderer."""
    assert "\n  " in render_crawl_json(make_report())
