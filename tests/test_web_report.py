"""Tests for the crawl report and its statistics."""

from collections import Counter
from datetime import UTC, datetime

import pytest

from maxicrawler.crawler import DiscoverySummary, PluginUsage
from maxicrawler.domain import ScanSession, Statistics
from maxicrawler.web.report import CrawlReport, CrawlStatistics, PageOutcome, SkipReason
from maxicrawler.web.session import CrawlOptions, CrawlSession, CrawlState

STARTED = datetime(2026, 8, 7, tzinfo=UTC)


def make_report(
    *,
    state: CrawlState = CrawlState.COMPLETED,
    statistics: CrawlStatistics | None = None,
    pages: tuple[PageOutcome, ...] = (),
    discovered: int = 30,
    duplicates: int = 7,
    visited: int = 3,
) -> CrawlReport:
    """Return a crawl report without running a crawl."""
    session = CrawlSession(
        session_id="crawl-1",
        seed_url="https://example.test/",
        started_at=STARTED,
        options=CrawlOptions(max_depth=2),
    )
    summary = DiscoverySummary(
        session=ScanSession("crawl-1", STARTED),
        statistics=Statistics(
            documents_processed=visited,
            discovered_urls=discovered,
            duplicate_urls=duplicates,
        ),
        plugin_usage=(PluginUsage("generic", 28), PluginUsage("mega", 2)),
    )
    return CrawlReport(
        session=session,
        state=state,
        statistics=statistics or CrawlStatistics(pages_visited=visited),
        summary=summary,
        pages=pages,
        finished_at=STARTED,
    )


# --- page outcomes -----------------------------------------------------------


def test_a_read_page_is_a_success() -> None:
    page = PageOutcome(url="https://example.test/a", depth=0, status=200)

    assert page.succeeded is True


def test_a_failed_page_carries_its_error() -> None:
    page = PageOutcome(url="https://example.test/a", depth=1, error="HTTP 404")

    assert page.succeeded is False
    assert page.error == "HTTP 404"


def test_a_redirected_page_reports_both_urls() -> None:
    page = PageOutcome(
        url="https://example.test/old",
        final_url="https://example.test/new",
        depth=0,
        status=200,
    )

    assert page.was_redirected is True


def test_a_page_that_did_not_move_reports_no_redirect() -> None:
    page = PageOutcome(url="https://example.test/a", final_url="https://example.test/a", depth=0)

    assert page.was_redirected is False


def test_a_page_that_never_answered_reports_no_redirect() -> None:
    page = PageOutcome(url="https://example.test/a", depth=0, error="unreachable")

    assert page.was_redirected is False


def test_a_page_records_a_canonical_claim_without_acting_on_it() -> None:
    page = PageOutcome(
        url="https://example.test/a?utm_source=x",
        final_url="https://example.test/a?utm_source=x",
        depth=1,
        canonical_url="https://example.test/a",
    )

    assert page.canonical_url == "https://example.test/a"
    assert page.final_url != page.canonical_url


def test_a_page_outcome_is_immutable() -> None:
    page = PageOutcome(url="https://example.test/a", depth=0)

    with pytest.raises(AttributeError):
        page.depth = 2  # type: ignore[misc]


# --- statistics --------------------------------------------------------------


def test_statistics_total_the_skips_they_were_given() -> None:
    skips: Counter[SkipReason] = Counter(
        {SkipReason.OUT_OF_SCOPE: 96, SkipReason.ALREADY_SEEN: 30, SkipReason.TOO_DEEP: 2}
    )

    statistics = CrawlStatistics.of(
        pages_visited=14,
        pages_failed=1,
        skips=skips,
        max_depth_reached=2,
        frontier_remaining=0,
        elapsed_seconds=6.2,
    )

    assert statistics.pages_skipped == 128
    assert statistics.pages_attempted == 15


def test_skips_are_ordered_by_frequency_then_by_name() -> None:
    skips: Counter[SkipReason] = Counter(
        {SkipReason.TOO_DEEP: 5, SkipReason.ALREADY_SEEN: 5, SkipReason.OUT_OF_SCOPE: 9}
    )

    statistics = CrawlStatistics.of(
        pages_visited=1,
        pages_failed=0,
        skips=skips,
        max_depth_reached=1,
        frontier_remaining=0,
        elapsed_seconds=0.1,
    )

    assert statistics.skips_by_reason == (
        (SkipReason.OUT_OF_SCOPE, 9),
        (SkipReason.ALREADY_SEEN, 5),
        (SkipReason.TOO_DEEP, 5),
    )


def test_statistics_without_skips_report_none() -> None:
    statistics = CrawlStatistics.of(
        pages_visited=1,
        pages_failed=0,
        skips=Counter(),
        max_depth_reached=0,
        frontier_remaining=0,
        elapsed_seconds=0.1,
    )

    assert statistics.skips_by_reason == ()
    assert statistics.pages_skipped == 0


def test_a_skip_reason_renders_as_readable_text() -> None:
    assert str(SkipReason.OUT_OF_SCOPE) == "out of scope"


# --- the report --------------------------------------------------------------


def test_the_report_composes_the_shared_discovery_summary() -> None:
    report = make_report()

    assert report.summary.documents_processed == 3
    assert report.summary.unique_urls == 30
    assert report.summary.duplicates_removed == 7
    assert report.summary.plugin_usage[0].name == "generic"


def test_pages_visited_agrees_with_documents_processed() -> None:
    """The two views are the same number, not two numbers that must be added."""
    report = make_report(visited=14)

    assert report.pages_visited == report.summary.documents_processed


def test_the_report_totals_the_links_it_found() -> None:
    report = make_report(discovered=30, duplicates=7)

    assert report.links_discovered == 37


def test_the_report_names_the_seed() -> None:
    assert make_report().seed_url == "https://example.test/"


def test_a_completed_crawl_ran_out_of_work() -> None:
    assert make_report(state=CrawlState.COMPLETED).was_complete is True


@pytest.mark.parametrize("state", [CrawlState.PAGE_LIMIT, CrawlState.INTERRUPTED])
def test_a_crawl_that_hit_a_limit_is_not_complete(state: CrawlState) -> None:
    assert make_report(state=state).was_complete is False


def test_the_report_separates_failures_from_successes() -> None:
    pages = (
        PageOutcome(url="https://example.test/a", depth=0, status=200),
        PageOutcome(url="https://example.test/b", depth=1, error="HTTP 404"),
        PageOutcome(url="https://example.test/c", depth=1, status=200),
    )

    report = make_report(pages=pages)

    assert len(report.failures) == 1
    assert report.failures[0].url == "https://example.test/b"


def test_the_report_is_immutable() -> None:
    report = make_report()

    with pytest.raises(AttributeError):
        report.state = CrawlState.INTERRUPTED  # type: ignore[misc]


def test_the_report_does_not_copy_the_request_context() -> None:
    """The report keeps no context of its own.

    It does hold the session, and the session holds the context, so a
    credential added there later is reachable by traversal. The property that
    has to hold is narrower: nothing that *serializes* a report may write it.
    That is asserted where the renderer and the repository live.
    """
    report = make_report()

    assert "context" not in CrawlReport.__slots__
    assert report.session.context is report.session.context
