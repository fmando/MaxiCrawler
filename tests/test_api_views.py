"""Tests for the data a template renders.

Pure functions, so none of this needs HTTP, a database or a crawl.
"""

from datetime import UTC, datetime

import pytest

from maxicrawler.api.jobs import JobSnapshot
from maxicrawler.api.views import (
    KIND_LABELS,
    STATE_LABELS,
    STATE_TONES,
    crawl_rows,
    describe_options,
    describe_scope,
    format_duration,
    format_number,
    link_rows,
    link_table,
    page_rows,
    page_table,
    plugin_shares,
    progress_view,
    report_view,
)
from maxicrawler.crawler import DiscoverySummary, PluginUsage
from maxicrawler.database import StoredUrl
from maxicrawler.domain import ScanSession, Statistics, UrlRecord
from maxicrawler.web.models import LinkKind
from maxicrawler.web.report import CrawlReport, CrawlStatistics, PageOutcome, SkipReason
from maxicrawler.web.session import CrawlOptions, CrawlSession, CrawlState

STARTED = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def make_snapshot(**kwargs: object) -> JobSnapshot:
    """Return a snapshot of a running crawl."""
    values: dict[str, object] = {
        "job_id": "job-1",
        "seed_url": "https://example.test/",
        "state": CrawlState.RUNNING,
        "options": CrawlOptions(max_depth=2, max_pages=50),
        "started_at": STARTED,
    }
    values.update(kwargs)
    return JobSnapshot(**values)  # type: ignore[arg-type]


def make_report(**kwargs: object) -> CrawlReport:
    """Return a finished crawl report."""
    session = CrawlSession(
        session_id="job-1",
        seed_url="https://example.test/",
        started_at=STARTED,
        options=CrawlOptions(max_depth=2, max_pages=50, same_domain=True),
    )
    values: dict[str, object] = {
        "session": session,
        "state": CrawlState.COMPLETED,
        "statistics": CrawlStatistics(
            pages_visited=28,
            pages_failed=2,
            pages_attempted=31,
            pages_skipped=4760,
            skips_by_reason=((SkipReason.NOT_A_PAGE, 2616), (SkipReason.ALREADY_SEEN, 2144)),
            links_by_kind=((LinkKind.ANCHOR, 5022), (LinkKind.IMAGE, 2502)),
            max_depth_reached=2,
            frontier_remaining=7,
            elapsed_seconds=18.84,
        ),
        "summary": DiscoverySummary(
            session=ScanSession("job-1", STARTED),
            statistics=Statistics(
                documents_processed=28, discovered_urls=2919, duplicate_urls=4729
            ),
            plugin_usage=(PluginUsage("generic", 1628), PluginUsage("mega", 1291)),
        ),
        "pages": (),
        "finished_at": STARTED,
    }
    values.update(kwargs)
    return CrawlReport(**values)  # type: ignore[arg-type]


# --- formatting --------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "0"), (7, "7"), (1291, "1,291"), (17910, "17,910"), (1000000, "1,000,000")],
)
def test_large_numbers_are_grouped(value: int, expected: str) -> None:
    assert format_number(value) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, "0.0 s"),
        (0.44, "0.4 s"),
        (18.84, "18.8 s"),
        (59.9, "59.9 s"),
        (60.0, "1 min 00 s"),
        (83.0, "1 min 23 s"),
        (412.7, "6 min 52 s"),
        (3600.0, "1 h 00 min"),
        (7530.0, "2 h 05 min"),
    ],
)
def test_durations_read_the_way_a_person_thinks(seconds: float, expected: str) -> None:
    """ "412.7 s" is a number nobody converts in their head."""
    assert format_duration(seconds) == expected


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        (CrawlOptions(), "any domain"),
        (CrawlOptions(same_domain=True), "same domain"),
        (CrawlOptions(same_domain=True, include_subdomains=True), "same domain and subdomains"),
        (CrawlOptions(include_subdomains=True), "any domain"),
    ],
)
def test_the_scope_is_described_in_one_phrase(options: CrawlOptions, expected: str) -> None:
    assert describe_scope(options) == expected


def test_the_options_line_says_what_a_crawl_was_told() -> None:
    options = CrawlOptions(max_depth=2, max_pages=1000, same_domain=True)

    assert describe_options(options) == "depth 2 · same domain · max 1,000 pages"


# --- the plugin distribution -------------------------------------------------


def test_host_plugins_come_before_the_fallback_however_the_counts_fall() -> None:
    """The one line this project exists to produce must not be buried."""
    shares = plugin_shares((PluginUsage("generic", 1628), PluginUsage("mega", 1291)))

    assert [share.name for share in shares] == ["mega", "generic"]
    assert shares[0].is_fallback is False
    assert shares[1].is_fallback is True


def test_several_host_plugins_are_ordered_by_count() -> None:
    shares = plugin_shares(
        (PluginUsage("generic", 500), PluginUsage("mega", 10), PluginUsage("pixeldrain", 40))
    )

    assert [share.name for share in shares] == ["pixeldrain", "mega", "generic"]


def test_equal_counts_are_ordered_by_name_so_the_page_does_not_jitter() -> None:
    shares = plugin_shares((PluginUsage("zeta", 5), PluginUsage("alpha", 5)))

    assert [share.name for share in shares] == ["alpha", "zeta"]


def test_a_share_is_a_fraction_of_everything_classified() -> None:
    shares = plugin_shares((PluginUsage("generic", 750), PluginUsage("mega", 250)))

    assert shares[0].share == pytest.approx(0.25)
    assert shares[0].percent == "25%"
    assert shares[1].percent == "75%"


def test_no_plugins_yields_no_shares() -> None:
    assert plugin_shares(()) == ()


def test_a_zero_total_does_not_divide_by_zero() -> None:
    shares = plugin_shares((PluginUsage("generic", 0),))

    assert shares[0].share == 0.0
    assert shares[0].percent == "0%"


# --- a running crawl ---------------------------------------------------------


def test_a_running_crawl_reports_its_counters() -> None:
    view = progress_view(make_snapshot(pages_visited=12, pages_failed=1, links_found=340))

    assert view["pages_visited"] == "12"
    assert view["pages_failed"] == "1"
    assert view["pages_attempted"] == "13"
    assert view["links_found"] == "340"
    assert view["state_label"] == "running"
    assert view["state_tone"] == "busy"


def test_a_running_crawl_groups_its_thousands() -> None:
    """The live update writes these straight into the page, so they arrive ready."""
    view = progress_view(make_snapshot(links_found=17910))

    assert view["links_found"] == "17,910"


def test_progress_is_a_whole_percentage_for_a_bar() -> None:
    view = progress_view(make_snapshot(pages_visited=12))

    assert view["progress_percent"] == 24


def test_a_finished_crawl_shows_a_full_bar() -> None:
    view = progress_view(make_snapshot(state=CrawlState.COMPLETED, pages_visited=3))

    assert view["progress_percent"] == 100
    assert view["is_finished"] is True
    assert view["state_label"] == "completed"


def test_a_crawl_that_never_started_is_shown_as_failed() -> None:
    view = progress_view(make_snapshot(error="HTTP 404 from https://example.test/"))

    assert view["state_label"] == "failed"
    assert view["state_tone"] == "bad"
    assert view["is_finished"] is True
    assert "404" in view["error"]


def test_the_running_view_carries_the_latest_page() -> None:
    view = progress_view(make_snapshot(latest_url="https://example.test/docs/"))

    assert view["latest_url"] == "https://example.test/docs/"


def test_the_running_view_formats_its_elapsed_time() -> None:
    assert progress_view(make_snapshot(elapsed_seconds=83.0))["elapsed"] == "1 min 23 s"


# --- a finished crawl --------------------------------------------------------


def test_a_report_carries_every_counter_the_page_shows() -> None:
    view = report_view(make_report())

    assert view["pages_visited"] == "28"
    assert view["pages_failed"] == "2"
    assert view["pages_attempted"] == "31"
    assert view["pages_skipped"] == "4,760"
    assert view["links_found"] == "7,648"
    assert view["unique_urls"] == "2,919"
    assert view["duplicates_removed"] == "4,729"
    assert view["max_depth_reached"] == 2
    assert view["frontier_remaining"] == "7"
    assert view["elapsed"] == "18.8 s"


def test_a_report_names_why_urls_were_skipped() -> None:
    view = report_view(make_report())

    assert view["skips"] == (
        {"reason": "not a page link", "count": "2,616"},
        {"reason": "already seen", "count": "2,144"},
    )


def test_a_report_names_how_links_were_written() -> None:
    view = report_view(make_report())

    assert view["link_kinds"] == (
        {"kind": "anchor", "count": "5,022"},
        {"kind": "image", "count": "2,502"},
    )


def test_a_report_puts_mega_before_generic() -> None:
    view = report_view(make_report())

    assert [share.name for share in view["plugins"]] == ["mega", "generic"]


def test_a_share_carries_what_the_bar_and_the_label_need() -> None:
    (mega, generic) = report_view(make_report())["plugins"]

    assert mega.count_label == "1,291"
    assert mega.percent == "44%"
    assert generic.width.endswith("%")


def test_a_share_of_almost_nothing_still_draws_something() -> None:
    """An invisible bar would say "none" where the count beside it says "4"."""
    mega, _generic = plugin_shares((PluginUsage("generic", 9996), PluginUsage("mega", 4)))

    assert mega.percent == "0%"
    assert mega.width == "0.40%"


def test_a_plugin_that_claimed_nothing_draws_no_bar() -> None:
    (share,) = plugin_shares((PluginUsage("generic", 0),))

    assert share.width == "0%"


def test_a_report_points_at_the_whole_document() -> None:
    assert report_view(make_report())["json_url"] == "/crawls/job-1.json"


def test_a_report_says_when_it_finished() -> None:
    assert report_view(make_report())["finished_at"] == "2026-08-09 12:00"


def test_a_report_that_ran_out_of_budget_says_so() -> None:
    assert report_view(make_report(state=CrawlState.PAGE_LIMIT))["hit_the_page_limit"] is True
    assert report_view(make_report())["hit_the_page_limit"] is False


def test_requests_that_produced_no_page_are_flagged_rather_than_compared() -> None:
    """The ceiling counts these, so a report that hides them explains nothing."""
    quiet = CrawlStatistics(pages_visited=4, pages_failed=1, pages_attempted=5)
    noisy = CrawlStatistics(pages_visited=4, pages_failed=1, pages_attempted=9)

    assert report_view(make_report(statistics=quiet))["had_answers_that_were_not_pages"] is False
    assert report_view(make_report(statistics=noisy))["had_answers_that_were_not_pages"] is True
    assert report_view(make_report(statistics=noisy))["requests_without_a_page"] == "4"


@pytest.mark.parametrize(
    ("state", "label", "tone"),
    [
        (CrawlState.COMPLETED, "completed", "good"),
        (CrawlState.PAGE_LIMIT, "page limit", "warn"),
        (CrawlState.INTERRUPTED, "stopped", "warn"),
    ],
)
def test_every_ending_gets_a_badge(state: CrawlState, label: str, tone: str) -> None:
    view = report_view(make_report(state=state))

    assert view["state_label"] == label
    assert view["state_tone"] == tone


# --- the page table ----------------------------------------------------------


def test_pages_become_rows_in_the_order_they_were_reached() -> None:
    pages = (
        PageOutcome(
            url="https://example.test/", depth=0, status=200, final_url="https://example.test/"
        ),
        PageOutcome(
            url="https://example.test/a", depth=1, status=200, final_url="https://example.test/a"
        ),
    )

    rows = page_rows(make_report(pages=pages))

    assert [row["url"] for row in rows] == ["https://example.test/", "https://example.test/a"]
    assert [row["depth"] for row in rows] == [0, 1]


def test_a_failed_page_says_so_without_a_status() -> None:
    pages = (PageOutcome(url="https://example.test/gone", depth=1, error="HTTP 404"),)

    (row,) = page_rows(make_report(pages=pages))

    assert row["status_label"] == "err"
    assert row["succeeded"] is False
    assert row["error"] == "HTTP 404"


def test_a_redirected_page_carries_both_urls() -> None:
    pages = (
        PageOutcome(
            url="https://example.test/old",
            final_url="https://example.test/new",
            depth=0,
            status=200,
        ),
    )

    (row,) = page_rows(make_report(pages=pages))

    assert row["was_redirected"] is True
    assert row["final_url"] == "https://example.test/new"


def test_the_page_table_can_be_limited() -> None:
    pages = tuple(
        PageOutcome(url=f"https://example.test/{index}", depth=1, status=200) for index in range(10)
    )

    assert len(page_rows(make_report(pages=pages), limit=3)) == 3


def test_a_crawl_with_no_pages_yields_no_rows() -> None:
    assert page_rows(make_report()) == ()


def test_the_page_table_says_how_many_it_left_out() -> None:
    pages = tuple(
        PageOutcome(url=f"https://example.test/{index}", depth=1, status=200) for index in range(12)
    )

    table = page_table(make_report(pages=pages), limit=5)

    assert len(table["rows"]) == 5
    assert table["total"] == "12"
    assert table["hidden"] == "7"
    assert table["has_hidden"] is True


def test_a_page_table_that_shows_everything_says_nothing_about_hiding() -> None:
    pages = (PageOutcome(url="https://example.test/", depth=0, status=200),)

    table = page_table(make_report(pages=pages), limit=5)

    assert table["has_hidden"] is False
    assert table["hidden"] == "0"


# --- the link table ----------------------------------------------------------


def make_stored_url(url: str, plugin: str | None, category: str | None = "share") -> StoredUrl:
    """Return a discovered URL as the database holds it."""
    return StoredUrl(
        record=UrlRecord(raw_url=url, normalized_url=url, source_url="https://example.test/"),
        plugin_name=plugin,
        category=category,
    )


def test_recorded_urls_become_rows() -> None:
    stored = make_stored_url("https://mega.nz/file/AaBbCcDd", "mega")

    (row,) = link_rows([stored])

    assert row["url"] == "https://mega.nz/file/AaBbCcDd"
    assert row["plugin"] == "mega"
    assert row["category"] == "share"
    assert row["source_url"] == "https://example.test/"
    assert row["is_notable"] is True


def test_a_link_the_generic_plugin_claimed_is_not_notable() -> None:
    (row,) = link_rows([make_stored_url("https://example.test/a", "generic")])

    assert row["is_notable"] is False


def test_a_link_no_plugin_claimed_says_so() -> None:
    (row,) = link_rows([make_stored_url("https://example.test/a", None, None)])

    assert row["plugin"] == "unresolved"
    assert row["is_notable"] is False


def test_a_normalized_link_keeps_what_was_written() -> None:
    stored = StoredUrl(
        record=UrlRecord(
            raw_url="https://Example.test/A?b=1#frag", normalized_url="https://example.test/A?b=1"
        ),
        plugin_name="generic",
        category=None,
    )

    (row,) = link_rows([stored])

    assert row["was_normalized"] is True
    assert row["raw_url"] == "https://Example.test/A?b=1#frag"


def test_host_plugins_come_before_the_fallback() -> None:
    """A table cut off at two hundred rows must not consist of generic links."""
    urls = [make_stored_url(f"https://example.test/{index}", "generic") for index in range(5)]
    urls.append(make_stored_url("https://mega.nz/file/AaBbCcDd", "mega"))
    urls.append(make_stored_url("https://example.test/x", None, None))

    rows = link_rows(urls)

    assert rows[0]["plugin"] == "mega"
    assert rows[-1]["plugin"] == "unresolved"


def test_discovery_order_survives_inside_a_group() -> None:
    urls = [make_stored_url(f"https://example.test/{index}", "generic") for index in range(4)]

    rows = link_rows(urls)

    assert [row["url"] for row in rows] == [f"https://example.test/{index}" for index in range(4)]


def test_the_link_table_says_how_many_it_left_out() -> None:
    urls = [make_stored_url(f"https://example.test/{index}", "generic") for index in range(9)]

    table = link_table(urls, discovered=9, limit=4)

    assert len(table["rows"]) == 4
    assert table["total"] == "9"
    assert table["hidden"] == "5"
    assert table["was_recorded"] is True


def test_a_crawl_that_recorded_nothing_is_not_a_crawl_that_found_nothing() -> None:
    """The difference the page has to state rather than show an empty table for."""
    table = link_table((), discovered=2919)

    assert table["rows"] == ()
    assert table["was_recorded"] is False
    assert table["discovered"] == "2,919"


def test_a_crawl_that_genuinely_found_nothing_says_that_instead() -> None:
    table = link_table((), discovered=0)

    assert table["rows"] == ()
    assert table["was_recorded"] is True


# --- the label tables --------------------------------------------------------


def test_every_state_has_a_label_and_a_tone() -> None:
    for state in CrawlState:
        assert state in STATE_LABELS
        assert state in STATE_TONES


def test_every_link_kind_has_a_label() -> None:
    for kind in LinkKind:
        assert kind in KIND_LABELS


def test_the_tones_stay_a_small_fixed_set() -> None:
    """CSS decides colour; this only decides which of four classes applies."""
    assert set(STATE_TONES.values()) <= {"idle", "busy", "good", "warn", "bad"}


# --- recorded crawls ---------------------------------------------------------


def make_stored_crawl(**kwargs: object):  # type: ignore[no-untyped-def]
    """Return a crawl summary as the database holds it."""
    from maxicrawler.database import StoredCrawl

    values: dict[str, object] = {
        "session_id": "crawl-1",
        "seed_url": "https://example.test/",
        "started_at": STARTED,
        "finished_at": STARTED,
        "state": CrawlState.COMPLETED,
        "max_depth": 2,
        "max_pages": 50,
        "same_domain": True,
        "include_subdomains": False,
        "pages_visited": 28,
        "pages_failed": 2,
        "pages_attempted": 31,
        "pages_skipped": 4760,
        "links_discovered": 17910,
        "max_depth_reached": 2,
        "frontier_remaining": 0,
        "elapsed_seconds": 18.84,
    }
    values.update(kwargs)
    return StoredCrawl(**values)  # type: ignore[arg-type]


def test_a_recorded_crawl_becomes_a_readable_row() -> None:
    (row,) = crawl_rows([make_stored_crawl()])

    assert row["seed_url"] == "https://example.test/"
    assert row["state_label"] == "completed"
    assert row["options"] == "depth 2 · same domain · max 50 pages"
    assert row["elapsed"] == "18.8 s"
    assert row["started_at"] == "2026-08-09 12:00"


def test_large_counts_in_a_row_are_grouped() -> None:
    """A five-digit number in a column is what makes a table unreadable."""
    (row,) = crawl_rows([make_stored_crawl()])

    assert row["links_found"] == "17,910"
    assert row["pages_visited"] == "28"


def test_a_row_says_whether_anything_failed() -> None:
    without = crawl_rows([make_stored_crawl(pages_failed=0)])[0]
    with_failures = crawl_rows([make_stored_crawl(pages_failed=2)])[0]

    assert without["has_failures"] is False
    assert with_failures["has_failures"] is True


def test_a_crawl_still_running_is_marked() -> None:
    (row,) = crawl_rows([make_stored_crawl(finished_at=None, state=CrawlState.RUNNING)])

    assert row["is_running"] is True
    assert row["state_label"] == "running"


def test_a_row_survives_options_an_older_release_never_recorded() -> None:
    """A summary written before a validation rule must stay readable."""
    (row,) = crawl_rows([make_stored_crawl(max_pages=0)])

    assert "max 1 pages" in row["options"]


def test_no_recorded_crawls_yields_no_rows() -> None:
    assert crawl_rows([]) == ()


def test_a_stopped_crawl_does_not_claim_a_full_bar() -> None:
    """48 of 50 pages with a full bar would be claiming it finished."""
    view = progress_view(
        make_snapshot(
            state=CrawlState.INTERRUPTED, pages_visited=48, options=CrawlOptions(max_pages=50)
        )
    )

    assert view["progress_percent"] == 96
    assert view["is_finished"] is True
    assert view["state_label"] == "stopped"


def test_a_completed_crawl_reads_as_full_however_little_it_used() -> None:
    """There the budget was never the limit -- the work ran out."""
    view = progress_view(
        make_snapshot(
            state=CrawlState.COMPLETED, pages_visited=6, options=CrawlOptions(max_pages=50)
        )
    )

    assert view["progress_percent"] == 100


def test_a_crawl_that_never_started_shows_an_empty_bar() -> None:
    view = progress_view(make_snapshot(error="HTTP 404"))

    assert view["progress_percent"] == 0
