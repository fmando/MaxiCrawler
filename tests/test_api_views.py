"""Tests for the data a template renders.

Pure functions, so none of this needs HTTP, a database or a crawl.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from maxicrawler.api.downloads import DownloadSnapshot, QueueSnapshot, QueueTally
from maxicrawler.api.jobs import JobSnapshot
from maxicrawler.api.views import (
    ABANDONED_LABEL,
    KIND_LABELS,
    LINK_COLUMNS,
    LINK_PARAMS,
    LINK_STATE_LABELS,
    LINK_STATE_TONES,
    PAGE_PARAMS,
    PANELS,
    STATE_LABELS,
    STATE_TONES,
    TRANSIENT_PARAMS,
    QueuedBatch,
    crawl_rows,
    describe_options,
    describe_scope,
    download_view,
    format_bytes,
    format_duration,
    format_number,
    library_view,
    link_rows,
    link_view,
    page_rows,
    page_view,
    panel_view,
    plugin_shares,
    progress_view,
    queue_follow,
    queue_strip,
    report_view,
    settings_view,
    stored_view,
)
from maxicrawler.app import (
    UNTRACKED,
    DownloadProgress,
    DownloadSummary,
    LibraryItem,
    LibraryPage,
    LibraryQuery,
    LibrarySort,
    LinkFacet,
    LinkItem,
    LinkPage,
    LinkQuery,
    LinkSort,
    LinkState,
    PageQuery,
    PageState,
    TargetKind,
    browse_pages,
    target_of,
)
from maxicrawler.crawler import DiscoverySummary, PluginUsage
from maxicrawler.domain import DownloadStatus, ScanSession, Statistics
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
        (CrawlOptions(below_seed=True), "below the start URL"),
        (CrawlOptions(below_seed=True, same_domain=True), "below the start URL"),
    ],
)
def test_the_scope_is_described_in_one_phrase(options: CrawlOptions, expected: str) -> None:
    assert describe_scope(options) == expected


def test_the_options_line_says_what_a_crawl_was_told() -> None:
    options = CrawlOptions(max_depth=2, max_pages=1000, same_domain=True)

    assert describe_options(options) == (
        "depth 2 · same domain · max 1,000 pages · robots.txt obeyed"
    )


def test_the_options_line_says_when_robots_was_ignored() -> None:
    """Either way, never by silence: the default is what the reader lacks."""
    options = CrawlOptions(max_depth=2, max_pages=1000, same_domain=True, respect_robots=False)

    assert describe_options(options) == (
        "depth 2 · same domain · max 1,000 pages · robots.txt ignored"
    )


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

    rows = page_rows(pages)

    assert [row["url"] for row in rows] == ["https://example.test/", "https://example.test/a"]
    assert [row["depth"] for row in rows] == [0, 1]


def test_a_failed_page_says_so_without_a_status() -> None:
    pages = (PageOutcome(url="https://example.test/gone", depth=1, error="HTTP 404"),)

    (row,) = page_rows(pages)

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

    (row,) = page_rows(pages)

    assert row["was_redirected"] is True
    assert row["final_url"] == "https://example.test/new"


def test_a_crawl_with_no_pages_yields_no_rows() -> None:
    assert page_rows(()) == ()


def make_pages(count: int, *, failed: int = 0) -> tuple[PageOutcome, ...]:
    """Return *count* page outcomes, the last *failed* of them broken."""
    return tuple(
        PageOutcome(
            url=f"https://example.test/{index}",
            depth=1,
            status=None if index >= count - failed else 200,
            error="HTTP 404" if index >= count - failed else None,
        )
        for index in range(count)
    )


def make_page_view(
    pages: tuple[PageOutcome, ...],
    query: PageQuery | None = None,
    *,
    carry: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return the page table as one crawl's report renders it."""
    return page_view(browse_pages(pages, query), base=BASE, carry=carry or {})


def test_the_page_table_counts_the_page_against_the_crawl() -> None:
    view = make_page_view(make_pages(12), PageQuery(per_page=5))

    assert len(view["rows"]) == 5
    assert view["total"] == "12"
    assert view["recorded"] == "12"
    assert view["shown_range"] == "1–5"
    assert view["pages"] == "3"


def test_the_page_table_can_be_narrowed_to_the_failures() -> None:
    view = make_page_view(make_pages(9, failed=2), PageQuery(state=PageState.FAILED))

    assert len(view["rows"]) == 2
    assert view["total"] == "2"
    assert view["recorded"] == "9"
    assert view["is_filtered"] is True


def test_the_page_chips_count_the_whole_crawl() -> None:
    view = make_page_view(make_pages(9, failed=2))
    chips = {chip["label"]: chip for chip in view["chips"]}

    assert chips["read"]["count"] == "7"
    assert chips["failed"]["count"] == "2"
    assert chips["failed"]["tone"] == "bad"


def test_a_state_no_page_is_in_is_not_offered() -> None:
    view = make_page_view(make_pages(3))

    assert [chip["label"] for chip in view["chips"]] == ["read"]


def test_the_page_chip_you_are_standing_on_takes_the_filter_off_again() -> None:
    view = make_page_view(make_pages(9, failed=2), PageQuery(state=PageState.FAILED))
    chips = {chip["label"]: chip for chip in view["chips"]}

    assert chips["failed"]["active"] is True
    assert chips["failed"]["url"] == f"{BASE}#pages"


def test_the_page_table_leads_back_to_itself() -> None:
    view = make_page_view(make_pages(9, failed=2))

    assert view["action"] == f"{BASE}#pages"
    assert all(chip["url"].endswith("#pages") for chip in view["chips"])


def test_the_page_table_writes_its_own_parameters() -> None:
    """Its own, so the link table's survive beside them."""
    view = make_page_view(make_pages(12), PageQuery(state=PageState.FAILED, per_page=5))
    chips = {chip["label"]: chip for chip in view["chips"]}

    assert "pstate=" in chips["read"]["url"]
    assert "state=" not in chips["read"]["url"].replace("pstate=", "")


def test_the_page_table_carries_the_link_filter_through_its_own_links() -> None:
    """Filtering one table must not throw the other table's filter away."""
    view = make_page_view(make_pages(9, failed=2), carry={"plugin": "mega", "q": "pdf"})
    chips = {chip["label"]: chip for chip in view["chips"]}

    assert "plugin=mega" in chips["failed"]["url"]
    assert "q=pdf" in chips["failed"]["url"]
    assert view["carried"] == (
        {"name": "plugin", "value": "mega"},
        {"name": "q", "value": "pdf"},
    )


def test_clearing_the_page_filter_keeps_the_link_filter() -> None:
    view = make_page_view(make_pages(9, failed=2), carry={"plugin": "mega"})

    assert view["reset_url"] == f"{BASE}?plugin=mega#pages"


# --- the link table ----------------------------------------------------------


def make_link(
    url: str, plugin: str | None = "generic", category: str | None = "share", *, position: int = 0
) -> LinkItem:
    """Return one discovered URL as the service hands it over."""
    return LinkItem(
        url=url,
        raw_url=url,
        source_url="https://example.test/",
        plugin=plugin,
        category=category,
        target=target_of(url),
        position=position,
    )


def make_link_page(
    items: Sequence[LinkItem] = (),
    *,
    query: LinkQuery | None = None,
    total: int | None = None,
    discovered: int = 0,
    downloadable: Iterable[str] = (),
    pages: int = 1,
    page: int = 1,
    plugins: Iterable[LinkFacet] = (),
    targets: Iterable[LinkFacet] = (),
    states: Iterable[LinkFacet] = (),
    known: Mapping[LinkState, Iterable[str]] | None = None,
) -> LinkPage:
    """Return a page of links the way the service would have built one.

    *total* defaults to what is shown, so a test that is not about paging says
    nothing about it. Ordering, filtering and paging are the service's subject
    and are tested in `test_app_discovery.py`; this file is about wording and
    about the links each view builds.
    """
    return LinkPage(
        items=tuple(items),
        query=query if query is not None else LinkQuery(),
        total=len(items) if total is None else total,
        recorded=len(items),
        discovered=discovered,
        page=page,
        pages=pages,
        plugins=tuple(plugins),
        targets=tuple(targets),
        states=tuple(states),
        downloadable=frozenset(downloadable),
        known={state: frozenset(urls) for state, urls in (known or {}).items()},
    )


def make_link_view(page: LinkPage, *, hidden: Iterable[str] = ()) -> dict[str, Any]:
    """Return the link table as one crawl's report renders it."""
    return link_view(page, base=BASE, hidden=frozenset(hidden))


BASE = "/crawls/abc"
"""The report every link view here belongs to."""


def test_the_download_filter_is_offered_where_it_separates_something() -> None:
    view = make_link_view(make_link_page())

    assert [label for _, label in view["downloadable_choices"]] == [
        "any",
        "can be downloaded",
        "cannot",
    ]


def test_the_download_filter_is_withdrawn_where_everything_can_be_fetched() -> None:
    """One full bucket and one empty one is not a filter.

    Decided by the installation rather than by counting the rows on screen: one
    page of a crawl is not evidence about the crawl.
    """
    view = link_view(make_link_page(), base=BASE, downloads_everything=True)

    assert view["downloadable_choices"] == ()


def test_recorded_urls_become_rows() -> None:
    page = make_link_page([make_link("https://mega.nz/file/AaBbCcDd", "mega")])

    (row,) = link_rows(page)

    assert row["url"] == "https://mega.nz/file/AaBbCcDd"
    assert row["plugin"] == "mega"
    assert row["category"] == "share"
    assert row["source_url"] == "https://example.test/"
    assert row["is_notable"] is True


def test_a_link_the_generic_plugin_claimed_is_not_notable() -> None:
    (row,) = link_rows(make_link_page([make_link("https://example.test/a")]))

    assert row["is_notable"] is False


def test_a_link_no_plugin_claimed_is_given_a_word_here() -> None:
    """The service leaves it as nothing; what to call nothing is a wording choice."""
    (row,) = link_rows(make_link_page([make_link("https://example.test/a", None, None)]))

    assert row["plugin"] == "unresolved"
    assert row["category"] == "—"
    assert row["is_notable"] is False


def test_a_normalized_link_keeps_what_was_written() -> None:
    item = LinkItem(
        url="https://example.test/A?b=1",
        raw_url="https://Example.test/A?b=1#frag",
        source_url=None,
        plugin="generic",
        category=None,
        target=TargetKind.UNKNOWN,
        position=0,
    )

    (row,) = link_rows(make_link_page([item]))

    assert row["was_normalized"] is True
    assert row["raw_url"] == "https://Example.test/A?b=1#frag"


def test_a_url_is_shown_as_what_it_points_at() -> None:
    (row,) = link_rows(make_link_page([make_link("https://example.test/a.pdf")]))

    assert row["target"] == "documents"
    assert row["target_is_stated"] is True


def test_a_url_that_says_nothing_is_not_emphasised() -> None:
    """ "Not stated" is true of most URLs and is not worth a reader's eye."""
    (row,) = link_rows(make_link_page([make_link("https://example.test/a")]))

    assert row["target"] == "not stated"
    assert row["target_is_stated"] is False


def test_the_link_table_counts_the_page_against_the_crawl() -> None:
    items = [make_link(f"https://example.test/{index}", position=index) for index in range(4)]

    view = make_link_view(make_link_page(items, total=9, discovered=9, pages=3))

    assert len(view["rows"]) == 4
    assert view["total"] == "9"
    assert view["shown_range"] == "1–4"
    assert view["pages"] == "3"
    assert view["was_recorded"] is True


def test_a_crawl_that_recorded_nothing_is_not_a_crawl_that_found_nothing() -> None:
    """The difference the page has to state rather than show an empty table for."""
    view = make_link_view(make_link_page(discovered=2919))

    assert view["rows"] == ()
    assert view["has_rows"] is False
    assert view["has_any"] is False
    assert view["was_recorded"] is False
    assert view["discovered"] == "2,919"


def test_a_crawl_that_genuinely_found_nothing_says_that_instead() -> None:
    view = make_link_view(make_link_page(discovered=0))

    assert view["rows"] == ()
    assert view["was_recorded"] is True


def test_only_a_link_a_provider_could_fetch_offers_a_download() -> None:
    mega = "https://mega.nz/file/AaBbCcDd"
    items = [make_link(mega, "mega"), make_link("https://example.test/a", position=1)]

    view = make_link_view(make_link_page(items, downloadable=[mega]))

    assert [row["can_download"] for row in view["rows"]] == [True, False]
    assert view["has_downloads"] is True


def test_a_table_with_nothing_to_fetch_grows_no_column() -> None:
    """A column of empty cells is worse than no column."""
    view = make_link_view(make_link_page([make_link("https://example.test/a")]))

    assert view["has_downloads"] is False
    assert view["rows"][0]["can_download"] is False


# --- the links a report builds -----------------------------------------------


def test_an_untouched_report_writes_no_query_string() -> None:
    """Only what differs from the default is carried."""
    view = make_link_view(make_link_page([make_link("https://example.test/a")]))

    assert view["reset_url"] == f"{BASE}#links"


def test_every_link_leads_back_to_the_table() -> None:
    """Clicking a filter must not put you at the top of a report three screens long."""
    view = make_link_view(
        make_link_page([make_link("https://example.test/a")], plugins=[LinkFacet("mega", 3)])
    )

    (group,) = view["facets"]
    (chip,) = group["chips"]

    assert chip["url"].endswith("#links")
    assert view["action"] == f"{BASE}#links"


def test_a_chip_carries_its_count_and_the_query_that_selects_it() -> None:
    view = make_link_view(
        make_link_page([make_link("https://example.test/a")], plugins=[LinkFacet("mega", 1291)])
    )

    (chip,) = view["facets"][0]["chips"]

    assert chip["label"] == "mega"
    assert chip["count"] == "1,291"
    assert chip["active"] is False
    assert chip["url"] == f"{BASE}?plugin=mega#links"


def test_the_chip_you_are_standing_on_takes_the_filter_off_again() -> None:
    """A chip is a toggle rather than a one-way door."""
    view = make_link_view(
        make_link_page(
            [make_link("https://example.test/a")],
            query=LinkQuery(plugin="mega"),
            plugins=[LinkFacet("mega", 3)],
        )
    )

    (chip,) = view["facets"][0]["chips"]

    assert chip["active"] is True
    assert chip["url"] == f"{BASE}#links"


def test_a_target_chip_is_named_the_way_a_person_would_ask_for_it() -> None:
    view = make_link_view(
        make_link_page(
            [make_link("https://example.test/a.pdf")],
            targets=[LinkFacet("document", 2), LinkFacet("unknown", 40)],
        )
    )

    (group,) = view["facets"]

    assert group["heading"] == "Type"
    assert [chip["label"] for chip in group["chips"]] == ["documents", "not stated"]


def test_a_facet_nothing_falls_into_is_not_offered_at_all() -> None:
    view = make_link_view(make_link_page([make_link("https://example.test/a")]))

    assert view["facets"] == ()


def test_choosing_a_filter_returns_to_the_first_page() -> None:
    """Page four of the old question is not page four of the new one."""
    view = make_link_view(
        make_link_page(
            [make_link("https://example.test/a")],
            query=LinkQuery(page=4),
            plugins=[LinkFacet("mega", 3)],
            pages=9,
            page=4,
        )
    )

    (chip,) = view["facets"][0]["chips"]

    assert "page=" not in chip["url"]


def test_paging_keeps_the_filter_it_was_reached_with() -> None:
    view = make_link_view(
        make_link_page(
            [make_link("https://example.test/a")],
            query=LinkQuery(search="pdf", plugin="mega"),
            total=400,
            pages=4,
            page=2,
        )
    )

    assert "q=pdf" in view["next_url"]
    assert "plugin=mega" in view["next_url"]
    assert "page=3" in view["next_url"]
    assert "page=1" not in view["previous_url"]


def test_the_last_page_offers_no_next() -> None:
    view = make_link_view(make_link_page([make_link("https://example.test/a")]))

    assert view["next_url"] is None
    assert view["previous_url"] is None


# --- ordering and columns ----------------------------------------------------


def test_a_sortable_heading_is_a_link_and_the_others_are_not() -> None:
    view = make_link_view(make_link_page([make_link("https://example.test/a")]))
    headers = {header["name"]: header for header in view["headers"]}

    assert headers["url"]["url"] is not None
    assert headers["category"]["url"] is None


def test_clicking_the_active_column_reverses_it() -> None:
    view = make_link_view(
        make_link_page([make_link("https://example.test/a")], query=LinkQuery(sort=LinkSort.URL))
    )
    header = next(item for item in view["headers"] if item["name"] == "url")

    assert header["active"] is True
    assert "dir=desc" in header["url"]


def test_what_a_link_was_written_as_is_a_column_of_its_own() -> None:
    """It was a second line under the URL, which doubled the height of the table."""
    view = make_link_view(make_link_page([make_link("https://example.test/a")]))
    toggles = {toggle["name"]: toggle for toggle in view["toggles"]}

    assert "raw" in view["shown"]
    assert toggles["raw"]["label"] == "As written"
    assert "hide=raw" in toggles["raw"]["url"]


def test_the_raw_column_cannot_be_ordered_by() -> None:
    """It would order almost exactly as the URL beside it already does."""
    view = make_link_view(make_link_page([make_link("https://example.test/a")]))
    (header,) = [header for header in view["headers"] if header["name"] == "raw"]

    assert header["url"] is None


def test_every_column_but_the_url_can_be_turned_off() -> None:
    view = make_link_view(make_link_page([make_link("https://example.test/a")]))
    toggles = {toggle["name"]: toggle for toggle in view["toggles"]}

    assert toggles["url"]["required"] is True
    assert toggles["url"]["url"] is None
    assert all(toggle["shown"] for toggle in view["toggles"])
    assert "hide=plugin" in toggles["plugin"]["url"]


def test_a_hidden_column_is_left_out_and_offered_back() -> None:
    view = make_link_view(make_link_page([make_link("https://example.test/a")]), hidden=["source"])
    toggles = {toggle["name"]: toggle for toggle in view["toggles"]}

    assert "source" not in view["shown"]
    assert [header["name"] for header in view["headers"]] == [
        "plugin",
        "category",
        "target",
        "url",
        "raw",
    ]
    assert toggles["source"]["shown"] is False
    assert "hide=" not in toggles["source"]["url"]


def test_the_hidden_columns_survive_a_search() -> None:
    """They travel as a form field, so filtering does not undo the layout."""
    view = make_link_view(
        make_link_page([make_link("https://example.test/a")]), hidden=["source", "category"]
    )

    assert view["hide_value"] == "category,source"


def test_the_url_column_cannot_be_hidden_even_if_asked() -> None:
    view = make_link_view(make_link_page([make_link("https://example.test/a")]), hidden=["url"])

    assert "url" in view["shown"]
    assert any(header["name"] == "url" for header in view["headers"])


def test_every_column_is_offered_as_a_toggle() -> None:
    """A control that silently lacks an entry reads as a bug."""
    view = make_link_view(make_link_page(known={LinkState.IN_LIBRARY: ()}))

    assert [toggle["name"] for toggle in view["toggles"]] == [
        column.name for column in LINK_COLUMNS
    ]


# --- what is already known about a link --------------------------------------


def make_stateful_page(
    known: Mapping[LinkState, Iterable[str]],
    *,
    query: LinkQuery | None = None,
    states: Iterable[LinkFacet] = (),
) -> LinkPage:
    """Return a page of two links with *known* answered about them."""
    return make_link_page(
        [
            make_link("https://mega.nz/file/AaBbCcDd", "mega", position=0),
            make_link("https://mega.nz/file/EeFfGgHh", "mega", position=1),
        ],
        query=query,
        states=states,
        known=known,
    )


def test_a_row_says_what_is_known_about_its_url() -> None:
    page = make_stateful_page({LinkState.IN_LIBRARY: ["https://mega.nz/file/AaBbCcDd"]})

    stored, other = link_rows(page)

    assert [mark["label"] for mark in stored["states"]] == ["in library"]
    assert stored["states"][0]["tone"] == "good"
    assert [mark["label"] for mark in other["states"]] == ["new"]


def test_a_row_in_no_state_is_called_new_rather_than_left_blank() -> None:
    """A blank cell would be the third thing an empty cell means in this table."""
    page = make_stateful_page({LinkState.IN_LIBRARY: (), LinkState.IN_QUEUE: ()})

    for row in link_rows(page):
        assert [mark["label"] for mark in row["states"]] == ["new"]
        assert row["states"][0]["tone"] == "idle"


def test_a_row_can_wear_more_than_one_badge() -> None:
    """One file of a folder stored and another queued is both, not either."""
    url = "https://mega.nz/file/AaBbCcDd"
    page = make_stateful_page({LinkState.IN_QUEUE: [url], LinkState.IN_LIBRARY: [url]})

    row, _ = link_rows(page)

    assert [mark["label"] for mark in row["states"]] == ["in library", "in queue"]


def test_the_badges_follow_the_declared_order_whatever_order_they_were_resolved_in() -> None:
    """Two reports of one crawl must not put the same badges in different places."""
    url = "https://mega.nz/file/AaBbCcDd"
    first = link_rows(make_stateful_page({LinkState.IN_LIBRARY: [url], LinkState.IN_QUEUE: [url]}))
    second = link_rows(make_stateful_page({LinkState.IN_QUEUE: [url], LinkState.IN_LIBRARY: [url]}))

    assert first[0]["states"] == second[0]["states"]


def test_nothing_is_claimed_about_a_row_when_nothing_was_asked() -> None:
    """ "Nobody asked" and "the answer was none" must not render the same."""
    (row,) = link_rows(make_link_page([make_link("https://example.test/a")]))

    assert row["states"] == ()


def test_the_state_column_is_withdrawn_when_nothing_was_asked() -> None:
    view = make_link_view(make_link_page([make_link("https://example.test/a")]))

    assert "state" not in view["shown"]
    assert all(header["name"] != "state" for header in view["headers"])
    assert all(toggle["name"] != "state" for toggle in view["toggles"])
    assert view["facets"] == ()


def test_the_state_column_leads_because_it_is_what_decides_a_tick() -> None:
    view = make_link_view(make_stateful_page({LinkState.IN_LIBRARY: ()}))

    assert [header["name"] for header in view["headers"]][0] == "state"


def test_the_state_column_can_be_turned_off_like_any_other() -> None:
    view = link_view(
        make_stateful_page({LinkState.IN_LIBRARY: ()}), base=BASE, hidden=frozenset({"state"})
    )
    toggles = {toggle["name"]: toggle for toggle in view["toggles"]}

    assert "state" not in view["shown"]
    assert toggles["state"]["shown"] is False


def test_the_state_column_cannot_be_ordered_by() -> None:
    """A handful of values; grouping by them is what a chip already does."""
    view = make_link_view(make_stateful_page({LinkState.IN_LIBRARY: ()}))
    (header,) = [header for header in view["headers"] if header["name"] == "state"]

    assert header["url"] is None


def test_the_states_are_offered_as_chips_with_their_counts() -> None:
    view = make_link_view(
        make_stateful_page(
            {LinkState.IN_LIBRARY: ["https://mega.nz/file/AaBbCcDd"]},
            states=[LinkFacet(value=UNTRACKED, count=2918), LinkFacet(value="library", count=1)],
        )
    )
    (group,) = [row for row in view["facets"] if row["heading"] == "State"]

    assert [chip["label"] for chip in group["chips"]] == ["new", "in library"]
    assert [chip["count"] for chip in group["chips"]] == ["2,918", "1"]
    assert "state=%28new%29" in group["chips"][0]["url"]
    assert "state=library" in group["chips"][1]["url"]


def test_the_chip_you_are_standing_on_leads_back_to_the_whole_crawl() -> None:
    view = make_link_view(
        make_stateful_page(
            {LinkState.IN_LIBRARY: ()},
            query=LinkQuery(state="library"),
            states=[LinkFacet(value="library", count=1)],
        )
    )
    (group,) = [row for row in view["facets"] if row["heading"] == "State"]

    assert group["chips"][0]["active"] is True
    assert "state=" not in group["chips"][0]["url"]


def test_the_state_filter_survives_a_search() -> None:
    """It travels as a hidden field, so typing a search does not undo it."""
    view = make_link_view(
        make_stateful_page({LinkState.IN_LIBRARY: ()}, query=LinkQuery(state=UNTRACKED))
    )

    assert view["state"] == UNTRACKED


def test_queueing_every_match_carries_the_state_it_is_looking_at() -> None:
    """The filter goes in the action, so the button queues what is on screen."""
    view = make_link_view(
        make_stateful_page({LinkState.IN_LIBRARY: ()}, query=LinkQuery(state=UNTRACKED))
    )

    assert "state=%28new%29" in view["matches_action"]


def test_a_state_nobody_named_is_shown_as_itself() -> None:
    """Adding a member must not be able to take the report down with it."""
    view = make_link_view(
        make_stateful_page({LinkState.IN_LIBRARY: ()}, states=[LinkFacet(value="hash", count=4)])
    )
    (group,) = [row for row in view["facets"] if row["heading"] == "State"]

    assert group["chips"][0]["label"] == "hash"


# --- folding a panel away ------------------------------------------------------


def test_every_panel_starts_open() -> None:
    panels = panel_view(frozenset(), base=BASE)

    assert set(panels) == set(PANELS)
    assert all(panel["is_open"] for panel in panels.values())
    assert all(panel["label"] == "Collapse" for panel in panels.values())


def test_a_folded_panel_says_so_and_offers_the_way_back() -> None:
    panels = panel_view(frozenset({"pages"}), base=BASE)

    assert panels["pages"]["is_open"] is False
    assert panels["pages"]["label"] == "Expand"
    assert panels["pages"]["url"] == f"{BASE}#pages"
    assert panels["links"]["url"] == f"{BASE}?shut=pages%2Clinks#links"


def test_folding_a_panel_leaves_everything_else_where_it_was() -> None:
    """Filtering, sorting, paging and columns all outlive a fold."""
    panels = panel_view(
        frozenset(), base=BASE, carry={"plugin": "mega", "sort": "url", "hide": "source"}
    )

    assert panels["pages"]["url"] == (f"{BASE}?plugin=mega&sort=url&hide=source&shut=pages#pages")


def test_a_link_lands_on_the_panel_it_just_changed() -> None:
    """A control that scrolls away from itself is one nobody uses twice."""
    panels = panel_view(frozenset(), base=BASE)

    for name, panel in panels.items():
        assert panel["url"].endswith(f"#{name}")


def test_an_untouched_report_writes_no_fold_state() -> None:
    """The default is nothing in the URL, the same way every other default is."""
    panels = panel_view(frozenset({"summary"}), base=BASE)

    assert panels["summary"]["url"] == f"{BASE}#summary"


def test_the_panels_are_written_in_the_order_they_appear() -> None:
    """So one report has one URL, whichever order the folds were clicked in."""
    panels = panel_view(frozenset({"links", "summary"}), base=BASE)

    assert "shut=summary%2Cpages%2Clinks" in panels["pages"]["url"]


# --- what the top bar says about the queue ------------------------------------


def test_an_idle_queue_leaves_the_top_bar_alone() -> None:
    assert queue_strip(QueueTally(running=0, waiting=0, failed=0)) is None


def test_a_busy_queue_says_what_it_is_doing() -> None:
    strip = queue_strip(QueueTally(running=1, waiting=1291, failed=0))

    assert [part["text"] for part in strip["parts"]] == ["1 downloading", "1,291 waiting"]
    assert [part["tone"] for part in strip["parts"]] == ["busy", "idle"]
    assert strip["url"] == "/downloads"


def test_a_part_that_is_zero_is_not_written() -> None:
    strip = queue_strip(QueueTally(running=0, waiting=4, failed=0))

    assert [part["text"] for part in strip["parts"]] == ["4 waiting"]


def test_failures_are_said_once_there_is_nothing_left_to_do() -> None:
    """The one thing about a finished queue somebody would otherwise miss."""
    strip = queue_strip(QueueTally(running=0, waiting=0, failed=2))

    assert [part["text"] for part in strip["parts"]] == ["2 failed"]
    assert strip["parts"][0]["tone"] == "bad"


def test_a_paused_queue_says_so_even_with_nothing_in_it() -> None:
    strip = queue_strip(QueueTally(running=0, waiting=0, failed=0, is_paused=True))

    assert [part["text"] for part in strip["parts"]] == ["paused"]


def test_paused_comes_last_because_it_is_the_reason_for_the_rest() -> None:
    strip = queue_strip(QueueTally(running=0, waiting=3, failed=1, is_paused=True))

    assert [part["text"] for part in strip["parts"]] == ["3 waiting", "1 failed", "paused"]


def test_the_strip_wears_only_tones_the_stylesheet_knows() -> None:
    strip = queue_strip(QueueTally(running=1, waiting=1, failed=1, is_paused=True))

    assert {part["tone"] for part in strip["parts"]} <= {"idle", "busy", "good", "warn", "bad"}


# --- what the queue page keeps watching ---------------------------------------


def make_queue_snapshot(**overrides: object) -> QueueSnapshot:
    """Return what a queue holds, with no queue behind it."""
    values: dict[str, object] = {"active": None, "waiting": (), "finished": ()}
    values.update(overrides)
    return QueueSnapshot(**values)  # type: ignore[arg-type]


def test_a_queue_with_nothing_left_to_do_is_not_watched() -> None:
    """A page that cannot change again has no reason to ask whether it did."""
    assert queue_follow(make_queue_snapshot()) is None


def test_a_running_transfer_is_what_the_page_listens_to() -> None:
    follow = queue_follow(make_queue_snapshot(active=make_download_snapshot()))

    assert follow["stream"] == "/downloads/d1/events"
    assert follow["swap"] == "/downloads?part=queue"
    assert follow["into"] == "queue"


def test_the_moment_between_two_transfers_is_asked_about_rather_than_listened_to() -> None:
    """The one case this exists for.

    The transfer that ended is finished and the next has not been picked up, so
    there is no stream — and a page that read that as "nothing left to do" would
    stop following a batch at whichever file lost the race.
    """
    follow = queue_follow(
        make_queue_snapshot(
            active=make_download_snapshot(summary=make_summary()),
            waiting=(make_download_snapshot(),),
        )
    )

    assert follow["stream"] is None
    assert follow["swap"] == "/downloads?part=queue"


def test_a_queue_with_something_waiting_and_nothing_running_asks_again() -> None:
    """The same gap, seen from the other side: the worker has not started yet."""
    follow = queue_follow(make_queue_snapshot(waiting=(make_download_snapshot(),)))

    assert follow["stream"] is None
    assert follow["into"] == "queue"


def test_a_paused_queue_is_not_watched_because_nothing_will_start() -> None:
    """Resuming is a form submission, and a form submission is a page load."""
    snapshot = make_queue_snapshot(waiting=(make_download_snapshot(),), is_paused=True)

    assert queue_follow(snapshot) is None


# --- the way back from a batch ------------------------------------------------


def test_a_batch_is_told_to_come_back_to_this_exact_view() -> None:
    view = make_link_view(
        make_link_page(query=LinkQuery(plugin="mega", search="share")), hidden=["source"]
    )

    assert view["return_to"] == f"{BASE}?q=share&plugin=mega&hide=source#links"


def test_a_report_reached_any_other_way_says_nothing_about_a_batch() -> None:
    assert make_link_view(make_link_page())["queued"] is None


def test_a_batch_that_went_through_whole_says_only_that() -> None:
    view = link_view(make_link_page(), base=BASE, queued=QueuedBatch(queued=1291))

    assert view["queued"]["sentence"] == "1,291 links queued."
    assert view["queued"]["notes"] == ()


def test_one_link_is_not_called_links() -> None:
    view = link_view(make_link_page(), base=BASE, queued=QueuedBatch(queued=1))

    assert view["queued"]["sentence"] == "1 link queued."


def test_a_batch_that_went_through_partly_names_the_remainder() -> None:
    """ "150 queued" alone leaves somebody to find the other fifty by counting."""
    view = link_view(
        make_link_page(), base=BASE, queued=QueuedBatch(queued=150, rejected=2, no_room=48)
    )

    assert view["queued"]["sentence"] == "150 links queued."
    assert view["queued"]["notes"] == (
        "48 did not fit — the queue is full.",
        "2 could not be fetched by the providers installed here.",
    )


def test_a_batch_that_queued_nothing_at_all_says_so() -> None:
    view = link_view(make_link_page(), base=BASE, queued=QueuedBatch(queued=0))

    assert view["queued"]["sentence"] == "Nothing was queued."


def test_a_confirmation_is_owned_by_neither_table() -> None:
    """Which is what makes it last one page: nothing carries it forward."""
    assert not TRANSIENT_PARAMS & LINK_PARAMS
    assert not TRANSIENT_PARAMS & PAGE_PARAMS


def test_nothing_a_link_is_built_from_can_carry_a_confirmation() -> None:
    view = make_link_view(make_link_page([make_link("https://example.test/a")]))
    built = [view["action"], view["reset_url"], view["matches_action"], view["return_to"]]

    assert all(param not in url for url in built for param in TRANSIENT_PARAMS)


# --- the label tables --------------------------------------------------------


def test_every_state_has_a_label_and_a_tone() -> None:
    for state in CrawlState:
        assert state in STATE_LABELS
        assert state in STATE_TONES


def test_every_link_state_has_a_label_and_a_tone() -> None:
    for state in [*LinkState, UNTRACKED]:
        assert state in LINK_STATE_LABELS
        assert state in LINK_STATE_TONES


def test_every_link_kind_has_a_label() -> None:
    for kind in LinkKind:
        assert kind in KIND_LABELS


def test_the_tones_stay_a_small_fixed_set() -> None:
    """CSS decides colour; this only decides which of four classes applies."""
    assert set(STATE_TONES.values()) <= {"idle", "busy", "good", "warn", "bad"}
    assert set(LINK_STATE_TONES.values()) <= {"idle", "busy", "good", "warn", "bad"}


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
        "below_seed": False,
        "respect_robots": True,
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
    assert row["options"] == "depth 2 · same domain · max 50 pages · robots.txt obeyed"
    assert row["elapsed"] == "18.8 s"
    assert row["started_at"] == "2026-08-09 12:00"


def test_a_recorded_crawl_reports_the_robots_setting_it_ran_under() -> None:
    """The stored column, not today's configuration.

    The setting can have changed since; the run cannot. A page reading the
    current value would answer a question about last month with this morning.
    """
    (row,) = crawl_rows([make_stored_crawl(respect_robots=False)])

    assert row["options"].endswith("robots.txt ignored")


def test_a_recorded_crawl_reports_the_scope_it_actually_ran_under() -> None:
    """A row that says "same domain" about a run confined to one path is a lie.

    Both columns are on the record, because both were submitted; which of them
    governed is the one question a reader has, and the row answers it.
    """
    (row,) = crawl_rows([make_stored_crawl(same_domain=True, below_seed=True)])

    assert "below the start URL" in row["options"]
    assert "same domain" not in row["options"]


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
    """Unfinished in the database *and* running here. Either alone is not enough."""
    (row,) = crawl_rows(
        [make_stored_crawl(finished_at=None, state=CrawlState.RUNNING)], live={"crawl-1"}
    )

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


# --- a crawl only the database remembers -------------------------------------


def test_a_crawl_nobody_is_running_is_not_running() -> None:
    """A record left unfinished by a killed process is not a live crawl."""
    crawl = make_stored_crawl(finished_at=None, state=CrawlState.RUNNING)

    (row,) = crawl_rows([crawl])

    assert row["state_label"] == ABANDONED_LABEL
    assert row["state_tone"] == "bad"
    assert row["was_abandoned"] is True
    assert row["is_running"] is False


def test_a_crawl_this_process_is_running_says_so() -> None:
    crawl = make_stored_crawl(finished_at=None, state=CrawlState.RUNNING)

    (row,) = crawl_rows([crawl], live={"crawl-1"})

    assert row["state_label"] == "running"
    assert row["is_running"] is True
    assert row["was_abandoned"] is False


def test_a_finished_crawl_is_never_called_abandoned() -> None:
    (row,) = crawl_rows([make_stored_crawl()])

    assert row["was_abandoned"] is False
    assert row["state_label"] == "completed"
    assert row["finished_at"] == "2026-08-09 12:00"


def test_a_crawl_with_no_end_shows_none() -> None:
    (row,) = crawl_rows([make_stored_crawl(finished_at=None)], live={"crawl-1"})

    assert row["finished_at"] == "—"


def test_the_recorded_view_carries_what_the_record_keeps() -> None:
    view = stored_view(make_stored_crawl())

    assert view["pages_visited"] == "28"
    assert view["pages_skipped"] == "4,760"
    assert view["links_found"] == "17,910"
    assert view["max_depth_reached"] == 2
    assert view["left_in_frontier"] is False


def test_the_recorded_view_says_when_work_was_left_over() -> None:
    view = stored_view(make_stored_crawl(frontier_remaining=7))

    assert view["left_in_frontier"] is True
    assert view["frontier_remaining"] == "7"


# --- the configuration -------------------------------------------------------


def flatten(groups: tuple[dict[str, object], ...]) -> dict[str, str]:
    """Return every configured value by name, whatever group it sits in."""
    return {row["name"]: row["value"] for group in groups for row in group["rows"]}  # type: ignore[index,union-attr]


def test_every_configured_value_is_shown() -> None:
    """A settings page that quietly omits a field is worse than none."""
    from maxicrawler.config import Settings

    shown = flatten(settings_view(Settings()))
    written = {line.split(" = ")[0] for line in Settings().to_toml().splitlines() if " = " in line}

    assert written <= set(shown)


def test_a_download_setting_is_filed_under_downloads() -> None:
    """The headings are how a reader finds a setting.

    `direct_downloads` governs what may be fetched, which is the other half of
    the program from a crawl default.
    """
    from maxicrawler.config import Settings

    sections = {
        section["heading"]: {row["name"] for row in section["rows"]}  # type: ignore[index,union-attr]
        for section in settings_view(Settings())
    }

    assert "direct_downloads" in sections["Downloads"]
    assert "direct_downloads" not in sections["Crawl defaults"]


def test_values_are_shown_the_way_they_are_written() -> None:
    from maxicrawler.config import Settings

    shown = flatten(settings_view(Settings(crawl_same_domain=True, crawl_max_pages=2500)))

    assert shown["crawl_same_domain"] == "true"
    assert shown["crawl_max_pages"] == "2,500"
    assert shown["max_page_bytes"] == "8 MiB"


def test_paths_are_shown_with_forward_slashes() -> None:
    """The same spelling the configuration file uses, on every platform."""
    from pathlib import Path

    from maxicrawler.config import Settings

    shown = flatten(settings_view(Settings(database_path=Path("var") / "urls.db")))

    assert shown["database_path"] == "var/urls.db"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (8 * 1024**2, "8 MiB"),
        (1024, "1 KiB"),
        (2 * 1024**2 + 1, "2,097,153 bytes"),
        (900, "900 bytes"),
    ],
)
def test_byte_counts_read_the_way_they_were_configured(value: int, expected: str) -> None:
    assert format_bytes(value) == expected


# --- one download -------------------------------------------------------------


def make_download_snapshot(
    *,
    status: DownloadStatus = DownloadStatus.RUNNING,
    written: int = 500_000,
    total: int | None = 1_300_000,
    files_total: int = 1,
    files_finished: int = 0,
    summary: DownloadSummary | None = None,
    error: str | None = None,
    was_started: bool = True,
) -> DownloadSnapshot:
    """Return a snapshot of a download that is not really happening.

    Started by default, because every download described here is one that
    got as far as moving bytes. A request still in the queue is the case
    that has to say so.
    """
    return DownloadSnapshot(
        download_id="d1",
        url="https://mega.nz/file/AaBbCcDd",
        progress=DownloadProgress(
            label="Jump.pdf",
            status=status,
            bytes_written=written,
            total_bytes=total,
            files_total=files_total,
            files_finished=files_finished,
        ),
        started_at=datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
        elapsed_seconds=12.5,
        was_started=was_started,
        summary=summary,
        error=error,
    )


def make_summary(**overrides: object) -> DownloadSummary:
    """Return a finished download's account."""
    values: dict[str, object] = {
        "url": "https://mega.nz/file/AaBbCcDd",
        "status": DownloadStatus.COMPLETED,
        "label": "Jump.pdf",
        "bytes_written": 1_300_000,
        "total_bytes": 1_300_000,
        "files_total": 1,
        "files_completed": 1,
        "path": Path("library") / "mega" / "abc" / "content" / "Jump.pdf",
    }
    values.update(overrides)
    return DownloadSummary(**values)  # type: ignore[arg-type]


def test_a_running_download_shows_both_counts_and_a_percentage() -> None:
    shown = download_view(make_download_snapshot())

    assert shown["transferred"] == "500.0 KB of 1.3 MB"
    assert shown["progress_percent"] == 38
    assert shown["has_total"] is True
    assert shown["state_label"] == "downloading"
    assert shown["state_tone"] == "busy"
    assert shown["elapsed"] == "12.5 s"


def test_a_download_whose_size_nobody_stated_has_no_percentage() -> None:
    """A bar at zero for two minutes would claim progress nobody can see."""
    shown = download_view(make_download_snapshot(total=None, written=1_300_000))

    assert shown["transferred"] == "1.3 MB"
    assert shown["progress_percent"] is None
    assert shown["has_total"] is False


def test_a_finished_download_reads_from_its_summary() -> None:
    shown = download_view(
        make_download_snapshot(
            status=DownloadStatus.RUNNING, summary=make_summary(), files_finished=1
        )
    )

    assert shown["is_finished"] is True
    assert shown["succeeded"] is True
    assert shown["state_label"] == "completed"
    assert shown["state_tone"] == "good"
    assert shown["path"] == "library/mega/abc/content/Jump.pdf"


def test_a_download_the_library_already_held_is_a_success() -> None:
    summary = make_summary(
        status=DownloadStatus.SKIPPED, bytes_written=0, reason="the library already holds it"
    )

    shown = download_view(make_download_snapshot(summary=summary))

    assert shown["state_label"] == "already stored"
    assert shown["state_tone"] == "good"
    assert shown["succeeded"] is True
    assert shown["reason"] == "the library already holds it"


def test_a_failed_download_carries_its_reason() -> None:
    summary = make_summary(status=DownloadStatus.FAILED, reason="the provider reports it as gone")

    shown = download_view(make_download_snapshot(summary=summary))

    assert shown["state_tone"] == "bad"
    assert shown["succeeded"] is False
    assert shown["reason"] == "the provider reports it as gone"


def test_a_download_that_broke_below_us_says_so_separately() -> None:
    """A failed transfer is a reason; a fault on our side is an error."""
    shown = download_view(make_download_snapshot(error="OSError: disk full"))

    assert shown["error"] == "OSError: disk full"
    assert shown["is_finished"] is True
    assert shown["state_label"] == "failed"


def test_a_link_holding_several_files_counts_them() -> None:
    shown = download_view(make_download_snapshot(files_total=5, files_finished=2))

    assert shown["has_many_files"] is True
    assert shown["files_total"] == "5"
    assert shown["files_finished"] == "2"


def test_one_file_is_not_worth_a_counter() -> None:
    assert download_view(make_download_snapshot())["has_many_files"] is False


# --- the library --------------------------------------------------------------


def make_item(name: str = "Jump.pdf", **overrides: object) -> LibraryItem:
    """Return one stored resource."""
    values: dict[str, object] = {
        "provider": "mega",
        "directory": "mega",
        "key": "abc",
        "name": name,
        "status": DownloadStatus.COMPLETED,
        "source_url": "https://mega.nz/file/AaBbCcDd",
        "filename": name,
        "size": 1_300_000,
        "downloaded_at": datetime(2026, 8, 9, 14, 30, tzinfo=UTC),
        "path": Path("library") / "mega" / "abc" / "content" / name,
        "checksum": "abc123",
    }
    values.update(overrides)
    return LibraryItem(**values)  # type: ignore[arg-type]


def library_page(items: tuple[LibraryItem, ...] = (), **overrides: object) -> LibraryPage:
    """Return a page of the library without reading one."""
    query = overrides.pop("query", LibraryQuery())
    assert isinstance(query, LibraryQuery)
    values: dict[str, object] = {
        "items": items,
        "query": query,
        "total": len(items),
        "stored": len(items),
        "page": 1,
        "pages": 1,
        "providers": tuple(sorted({item.directory for item in items})),
        "statuses": tuple(sorted({item.status for item in items})),
    }
    values.update(overrides)
    return LibraryPage(**values)  # type: ignore[arg-type]


def test_the_library_table_has_the_columns_it_promises() -> None:
    shown = library_view(library_page((make_item(),)))

    row = shown["rows"][0]
    assert row["provider"] == "mega"
    assert row["name"] == "Jump.pdf"
    assert row["size"] == "1.3 MB"
    assert row["downloaded_at"] == "2026-08-09 14:30"
    assert row["path"] == str(Path("library") / "mega" / "abc" / "content" / "Jump.pdf")
    assert row["state_label"] == "completed"
    assert row["url"] == "/library/mega/abc"


def test_a_failed_row_says_so_and_shows_no_path() -> None:
    item = make_item(status=DownloadStatus.FAILED, path=None, size=None)

    row = library_view(library_page((item,)))["rows"][0]

    assert row["state_label"] == "failed"
    assert row["state_tone"] == "bad"
    assert row["path"] == "—"
    assert row["size"] == "unknown"


def test_an_empty_library_has_no_rows() -> None:
    shown = library_view(library_page())

    assert shown["rows"] == ()
    assert shown["has_rows"] is False
    assert shown["stored"] == "0"
    assert shown["is_filtered"] is False


def test_a_filtered_listing_counts_both_numbers() -> None:
    shown = library_view(
        library_page((make_item(),), query=LibraryQuery(search="jump"), total=1, stored=9)
    )

    assert shown["total"] == "1"
    assert shown["stored"] == "9"
    assert shown["is_filtered"] is True
    assert shown["search"] == "jump"


# --- the links the table is navigated by --------------------------------------


def test_every_column_offers_a_link_that_sorts_by_it() -> None:
    shown = library_view(library_page((make_item(),)))

    labels = [column["label"] for column in shown["columns"]]
    assert labels == ["Provider", "Name", "Size", "Downloaded", "Status"]
    assert (
        "sort=name" in dict((column["label"], column["url"]) for column in shown["columns"])["Name"]
    )


def test_the_active_column_is_marked_and_its_link_reverses_it() -> None:
    shown = library_view(library_page((make_item(),), query=LibraryQuery(descending=True)))
    downloaded = next(column for column in shown["columns"] if column["label"] == "Downloaded")

    assert downloaded["active"] is True
    assert downloaded["mark"] == "▾"
    assert "dir=asc" in downloaded["url"]


def test_a_column_starts_in_the_direction_it_is_usually_wanted() -> None:
    """Largest file and newest download first; names from A."""
    shown = library_view(library_page((make_item(),), query=LibraryQuery()))
    columns = {column["label"]: column["url"] for column in shown["columns"]}

    assert "dir=asc" in columns["Name"]
    assert "dir=desc" in columns["Size"]


def test_an_unfiltered_default_listing_needs_no_query_string() -> None:
    """So the plain link in the navigation and a reset button agree."""
    assert library_view(library_page())["reset_url"] == "/library"


def test_the_filter_form_carries_the_current_order() -> None:
    shown = library_view(
        library_page((make_item(),), query=LibraryQuery(sort=LibrarySort.NAME, descending=False))
    )

    assert shown["sort_value"] == "name"
    assert shown["direction"] == "asc"


def test_paging_links_appear_only_where_there_is_a_page() -> None:
    items = (make_item(),)
    middle = library_view(library_page(items, total=9, page=2, pages=3))
    first = library_view(library_page(items, total=9, page=1, pages=3))
    last = library_view(library_page(items, total=9, page=3, pages=3))

    assert middle["previous_url"] is not None and middle["next_url"] is not None
    assert first["previous_url"] is None
    assert last["next_url"] is None
    assert "page=3" in (middle["next_url"] or "")


def test_a_page_says_which_rows_it_is_showing() -> None:
    shown = library_view(library_page((make_item(), make_item("b.pdf")), total=9, page=2))

    assert shown["page"] == "2"
    assert shown["pages"] == "1"
    assert "of 9" not in shown["shown"]
    assert shown["shown"].count("–") == 1


def test_a_filter_survives_a_sort_link() -> None:
    """Otherwise reordering a search silently searches for everything."""
    query = LibraryQuery(search="jump", provider="mega", status=DownloadStatus.COMPLETED)

    shown = library_view(library_page((make_item(),), query=query))
    url = next(column["url"] for column in shown["columns"] if column["label"] == "Name")

    assert "q=jump" in url
    assert "provider=mega" in url
    assert "status=completed" in url


# --- how fast, and how much longer --------------------------------------------


def test_a_transfer_that_has_run_long_enough_reports_a_rate() -> None:
    shown = download_view(make_download_snapshot(written=1_000_000))

    assert shown["rate"] == "80.0 KB/s"


def test_the_first_few_milliseconds_report_nothing() -> None:
    """Two chunks in fifty milliseconds divide out to a rate no line sustains."""
    snapshot = DownloadSnapshot(
        download_id="d1",
        url="https://mega.nz/file/AaBbCcDd",
        progress=DownloadProgress(
            label="Jump.pdf", status=DownloadStatus.RUNNING, bytes_written=4, total_bytes=1000
        ),
        started_at=datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
        elapsed_seconds=0.05,
    )

    shown = download_view(snapshot)

    assert shown["rate"] is None
    assert shown["remaining"] is None


def test_a_transfer_with_a_total_estimates_what_is_left() -> None:
    shown = download_view(make_download_snapshot(written=650_000, total=1_300_000))

    assert shown["remaining"] == "12.5 s"


def test_nothing_is_estimated_without_a_total() -> None:
    assert download_view(make_download_snapshot(total=None))["remaining"] is None


def test_a_finished_transfer_estimates_nothing() -> None:
    shown = download_view(make_download_snapshot(summary=make_summary()))

    assert shown["remaining"] is None


def test_a_transfer_that_has_arrived_in_full_estimates_nothing() -> None:
    """Zero seconds left is a claim; saying nothing is not."""
    shown = download_view(make_download_snapshot(written=1_300_000, total=1_300_000))

    assert shown["remaining"] is None
