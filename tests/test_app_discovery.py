"""Tests for the service that reads what a crawl discovered.

Most of these build rows directly rather than crawling for them: filtering,
ordering and paging are decisions about a set of records, and running a web
server to produce four of them would test the crawler again instead. The two
tests at the end do crawl, because "does what the crawl wrote come back out"
is the one question the rest of the file assumes.
"""

from collections.abc import Iterable
from pathlib import Path

from web_server import Site, serve

from maxicrawler.app import (
    UNRESOLVED,
    CrawlService,
    DiscoveryService,
    LinkQuery,
    LinkSort,
    TargetKind,
)
from maxicrawler.config import Settings
from maxicrawler.database import StoredUrl
from maxicrawler.domain import UrlRecord

MEGA_LINK = "https://mega.nz/file/AaBbCcDd#0123456789abcdefghijklmnopqrstuvwxyzABC"
SESSION = "session-1"


class FakeRepository:
    """A discovery repository holding exactly what a test put in it.

    Satisfies the one member :class:`DiscoveryService` calls, which is the whole
    reason the service takes a repository rather than a database path: a test
    about ordering has no business creating a file.
    """

    def __init__(self, urls: Iterable[StoredUrl] = ()) -> None:
        self.urls = tuple(urls)

    def stored_urls(self, session_id: str) -> tuple[StoredUrl, ...]:
        """Return the rows recorded for *session_id*."""
        return self.urls if session_id == SESSION else ()


def make_url(
    url: str,
    plugin: str | None = "generic",
    *,
    category: str | None = "share",
    raw_url: str | None = None,
    source_url: str | None = "https://example.test/",
) -> StoredUrl:
    """Return a discovered URL as the database holds it."""
    return StoredUrl(
        record=UrlRecord(
            raw_url=raw_url if raw_url is not None else url,
            normalized_url=url,
            source_url=source_url,
        ),
        plugin_name=plugin,
        category=category,
    )


def make_service(
    urls: Iterable[StoredUrl] = (), *, downloadable: Iterable[str] = ()
) -> DiscoveryService:
    """Return a service over the rows *urls*, with *downloadable* fetchable."""
    fetchable = frozenset(downloadable)
    return DiscoveryService(
        Settings(),
        repository=FakeRepository(urls),  # type: ignore[arg-type]
        downloadable=lambda candidates: frozenset(candidates) & fetchable,
    )


def urls_of(page: object) -> list[str]:
    """Return the URLs a page lists, in the order it lists them."""
    return [item.url for item in page.items]  # type: ignore[attr-defined]


# --- reading the rows --------------------------------------------------------


def test_recorded_rows_become_items() -> None:
    service = make_service([make_url(MEGA_LINK, "mega")])

    (item,) = service.links(SESSION)

    assert item.url == MEGA_LINK
    assert item.plugin == "mega"
    assert item.category == "share"
    assert item.source_url == "https://example.test/"
    assert item.position == 0
    assert item.is_notable is True


def test_a_link_the_generic_plugin_claimed_is_not_notable() -> None:
    (item,) = make_service([make_url("https://example.test/a")]).links(SESSION)

    assert item.is_notable is False
    assert item.facet == "generic"


def test_a_link_no_plugin_claimed_says_so() -> None:
    (item,) = make_service([make_url("https://example.test/a", None, category=None)]).links(SESSION)

    assert item.plugin is None
    assert item.facet == UNRESOLVED
    assert item.is_notable is False


def test_a_normalized_link_keeps_what_was_written() -> None:
    rows = [make_url("https://example.test/A?b=1", raw_url="https://Example.test/A?b=1#frag")]

    (item,) = make_service(rows).links(SESSION)

    assert item.was_normalized is True
    assert item.raw_url == "https://Example.test/A?b=1#frag"


def test_an_unknown_crawl_has_no_links() -> None:
    assert make_service([make_url("https://example.test/a")]).links("no-such-crawl") == ()


# --- ordering ----------------------------------------------------------------


def test_host_plugins_come_before_the_fallback() -> None:
    """A first page of two hundred rows must not consist of generic links."""
    rows = [make_url(f"https://example.test/{index}") for index in range(5)]
    rows.append(make_url(MEGA_LINK, "mega"))
    rows.append(make_url("https://example.test/x", None, category=None))

    page = make_service(rows).browse(SESSION)

    assert page.items[0].plugin == "mega"
    assert page.items[-1].plugin is None


def test_discovery_order_survives_inside_a_group() -> None:
    rows = [make_url(f"https://example.test/{index}") for index in range(4)]

    page = make_service(rows).browse(SESSION)

    assert urls_of(page) == [f"https://example.test/{index}" for index in range(4)]


def test_the_honest_discovery_order_is_available_on_its_own() -> None:
    rows = [make_url("https://example.test/a"), make_url(MEGA_LINK, "mega")]

    page = make_service(rows).browse(SESSION, LinkQuery(sort=LinkSort.DISCOVERED))

    assert urls_of(page) == ["https://example.test/a", MEGA_LINK]


def test_urls_can_be_ordered_by_themselves() -> None:
    rows = [make_url("https://example.test/c"), make_url("https://example.test/a")]

    page = make_service(rows).browse(SESSION, LinkQuery(sort=LinkSort.URL))

    assert urls_of(page) == ["https://example.test/a", "https://example.test/c"]


def test_a_url_found_on_no_page_sorts_last_in_either_direction() -> None:
    """ "Unknown" is not an early page, and reversing must not make it one."""
    rows = [
        make_url("https://example.test/a", source_url=None),
        make_url("https://example.test/b", source_url="https://example.test/one"),
    ]
    service = make_service(rows)

    ascending = service.browse(SESSION, LinkQuery(sort=LinkSort.SOURCE))
    descending = service.browse(SESSION, LinkQuery(sort=LinkSort.SOURCE, descending=True))

    assert urls_of(ascending)[-1] == "https://example.test/a"
    assert urls_of(descending)[-1] == "https://example.test/a"


# --- filtering ---------------------------------------------------------------


def test_a_search_matches_the_url_and_the_page_it_was_found_on() -> None:
    rows = [
        make_url("https://example.test/holiday.pdf"),
        make_url("https://example.test/other", source_url="https://example.test/holiday"),
        make_url("https://example.test/nothing", source_url="https://example.test/"),
    ]

    page = make_service(rows).browse(SESSION, LinkQuery(search="HOLIDAY"))

    assert page.total == 2
    assert "https://example.test/nothing" not in urls_of(page)


def test_a_search_matches_the_url_as_it_was_written() -> None:
    rows = [make_url("https://example.test/a", raw_url="https://Example.test/../a")]

    assert make_service(rows).browse(SESSION, LinkQuery(search="..")).total == 1


def test_filtering_by_plugin_keeps_only_that_plugin() -> None:
    rows = [make_url(MEGA_LINK, "mega"), make_url("https://example.test/a")]

    page = make_service(rows).browse(SESSION, LinkQuery(plugin="mega"))

    assert urls_of(page) == [MEGA_LINK]


def test_the_urls_nothing_claimed_are_reachable_as_a_filter() -> None:
    rows = [make_url("https://example.test/a"), make_url("mailto:x", None, category=None)]

    page = make_service(rows).browse(SESSION, LinkQuery(plugin=UNRESOLVED))

    assert urls_of(page) == ["mailto:x"]


def test_filtering_by_category_keeps_only_that_category() -> None:
    rows = [make_url("https://example.test/a"), make_url("https://example.test/b", category="page")]

    page = make_service(rows).browse(SESSION, LinkQuery(category="page"))

    assert urls_of(page) == ["https://example.test/b"]


def test_only_the_urls_a_provider_could_fetch() -> None:
    rows = [make_url(MEGA_LINK, "mega"), make_url("https://example.test/a")]
    service = make_service(rows, downloadable=[MEGA_LINK])

    assert urls_of(service.browse(SESSION, LinkQuery(downloadable=True))) == [MEGA_LINK]
    assert urls_of(service.browse(SESSION, LinkQuery(downloadable=False))) == [
        "https://example.test/a"
    ]


def test_filtering_by_what_a_url_points_at() -> None:
    rows = [
        make_url("https://example.test/report.pdf"),
        make_url("https://example.test/photo.jpg"),
        make_url("https://example.test/articles/holiday"),
    ]
    service = make_service(rows)

    assert urls_of(service.browse(SESSION, LinkQuery(target=TargetKind.DOCUMENT))) == [
        "https://example.test/report.pdf"
    ]
    assert urls_of(service.browse(SESSION, LinkQuery(target=TargetKind.IMAGE))) == [
        "https://example.test/photo.jpg"
    ]
    assert urls_of(service.browse(SESSION, LinkQuery(target=TargetKind.UNKNOWN))) == [
        "https://example.test/articles/holiday"
    ]


def test_an_item_carries_what_it_points_at() -> None:
    (item,) = make_service([make_url("https://example.test/a.zip")]).links(SESSION)

    assert item.target is TargetKind.ARCHIVE


def test_a_share_link_is_classified_by_its_path_and_not_by_its_key() -> None:
    """The fragment is a credential; it must never decide anything visible."""
    (item,) = make_service([make_url(MEGA_LINK)]).links(SESSION)

    assert item.target is TargetKind.UNKNOWN


def test_the_nearest_thing_to_a_duplicate_filter_is_what_normalization_changed() -> None:
    rows = [
        make_url("https://example.test/a", raw_url="https://EXAMPLE.test/a"),
        make_url("https://example.test/b"),
    ]

    page = make_service(rows).browse(SESSION, LinkQuery(normalized_only=True))

    assert urls_of(page) == ["https://example.test/a"]


def test_filters_combine() -> None:
    rows = [
        make_url(MEGA_LINK, "mega"),
        make_url("https://mega.nz/file/ZzZz", "mega"),
        make_url("https://example.test/AaBb"),
    ]

    page = make_service(rows).browse(SESSION, LinkQuery(plugin="mega", search="aabb"))

    assert urls_of(page) == [MEGA_LINK]


def test_an_unfiltered_query_says_so() -> None:
    assert LinkQuery().is_filtered is False
    assert LinkQuery(search="a").is_filtered is True
    assert LinkQuery(downloadable=False).is_filtered is True
    assert LinkQuery(normalized_only=True).is_filtered is True
    assert LinkQuery(target=TargetKind.IMAGE).is_filtered is True


# --- paging ------------------------------------------------------------------


def test_a_page_states_what_it_left_out() -> None:
    rows = [make_url(f"https://example.test/{index}") for index in range(9)]

    page = make_service(rows).browse(SESSION, LinkQuery(per_page=4), discovered=9)

    assert len(page.items) == 4
    assert page.total == 9
    assert page.hidden == 5
    assert page.pages == 3
    assert page.has_next is True
    assert page.has_previous is False
    assert (page.first, page.last) == (1, 4)


def test_the_second_page_continues_where_the_first_stopped() -> None:
    rows = [make_url(f"https://example.test/{index}") for index in range(9)]

    page = make_service(rows).browse(SESSION, LinkQuery(per_page=4, page=2))

    assert urls_of(page) == [f"https://example.test/{index}" for index in range(4, 8)]
    assert (page.first, page.last) == (5, 8)
    assert page.has_previous is True


def test_a_page_number_past_the_end_answers_with_the_last_one() -> None:
    """A stale bookmark is worth a listing, not a refusal."""
    rows = [make_url(f"https://example.test/{index}") for index in range(9)]

    page = make_service(rows).browse(SESSION, LinkQuery(per_page=4, page=99))

    assert page.page == 3
    assert len(page.items) == 1


def test_paging_orders_the_crawl_rather_than_the_page() -> None:
    """Sorting after cutting would sort whatever the cut happened to keep."""
    rows = [make_url(f"https://example.test/{index}") for index in range(5)]
    rows.append(make_url(MEGA_LINK, "mega"))

    page = make_service(rows).browse(SESSION, LinkQuery(per_page=1))

    assert urls_of(page) == [MEGA_LINK]


def test_an_unreasonable_page_size_is_capped() -> None:
    rows = [make_url(f"https://example.test/{index}") for index in range(3)]

    page = make_service(rows).browse(SESSION, LinkQuery(per_page=10_000_000))

    assert len(page.items) == 3
    assert page.pages == 1


# --- what a page says about the whole crawl ----------------------------------


def test_a_crawl_that_recorded_nothing_is_not_a_crawl_that_found_nothing() -> None:
    """The difference an interface has to state rather than show as an empty table."""
    page = make_service().browse(SESSION, discovered=2919)

    assert page.items == ()
    assert page.recorded == 0
    assert page.discovered == 2919
    assert page.was_recorded is False


def test_a_crawl_that_genuinely_found_nothing_says_that_instead() -> None:
    page = make_service().browse(SESSION, discovered=0)

    assert page.was_recorded is True


def test_the_facets_count_the_whole_crawl_rather_than_the_matches() -> None:
    """Choosing a filter must not remove the entry you would pick instead."""
    rows = [make_url(f"https://example.test/{index}") for index in range(3)]
    rows.append(make_url(MEGA_LINK, "mega"))

    page = make_service(rows).browse(SESSION, LinkQuery(plugin="mega"))

    assert [(facet.value, facet.count) for facet in page.plugins] == [("mega", 1), ("generic", 3)]
    assert page.total == 1


def test_the_fallback_and_the_unclaimed_come_after_the_host_plugins() -> None:
    rows = [make_url(f"https://example.test/{index}") for index in range(3)]
    rows.append(make_url("mailto:x", None, category=None))
    rows.append(make_url(MEGA_LINK, "mega"))

    page = make_service(rows).browse(SESSION)

    assert [facet.value for facet in page.plugins] == ["mega", "generic", UNRESOLVED]


def test_categories_are_counted_most_frequent_first() -> None:
    rows = [
        make_url("https://example.test/a", category="page"),
        make_url("https://example.test/b"),
        make_url("https://example.test/c", category="page"),
        make_url("https://example.test/d", category=None),
    ]

    page = make_service(rows).browse(SESSION)

    assert [(facet.value, facet.count) for facet in page.categories] == [("page", 2), ("share", 1)]


def test_targets_are_counted_in_the_order_the_kinds_are_declared() -> None:
    """Frequency would put "unknown" first on every crawl of every site."""
    rows = [
        make_url("https://example.test/one"),
        make_url("https://example.test/two"),
        make_url("https://example.test/a.jpg"),
        make_url("https://example.test/b.pdf"),
    ]

    page = make_service(rows).browse(SESSION)

    assert [(facet.value, facet.count) for facet in page.targets] == [
        ("document", 1),
        ("image", 1),
        ("unknown", 2),
    ]


def test_a_kind_nothing_points_at_is_not_offered() -> None:
    page = make_service([make_url("https://example.test/a.jpg")]).browse(SESSION)

    assert [facet.value for facet in page.targets] == ["image"]


def test_a_page_names_which_of_its_own_rows_can_be_fetched() -> None:
    rows = [make_url(MEGA_LINK, "mega"), make_url("https://example.test/a")]

    page = make_service(rows, downloadable=[MEGA_LINK]).browse(SESSION)

    assert page.downloadable == frozenset({MEGA_LINK})


def test_rows_not_on_this_page_are_not_asked_about() -> None:
    """The resolver is cheap but not free, and a page is what is being rendered."""
    asked: list[str] = []

    def resolver(candidates: Iterable[str]) -> frozenset[str]:
        seen = tuple(candidates)
        asked.extend(seen)
        return frozenset(seen)

    rows = [make_url(f"https://example.test/{index}") for index in range(9)]
    service = DiscoveryService(
        Settings(),
        repository=FakeRepository(rows),  # type: ignore[arg-type]
        downloadable=resolver,
    )

    service.browse(SESSION, LinkQuery(per_page=2))

    assert len(asked) == 2


def test_without_a_resolver_nothing_claims_to_be_downloadable() -> None:
    service = DiscoveryService(
        Settings(),
        repository=FakeRepository([make_url(MEGA_LINK, "mega")]),  # type: ignore[arg-type]
    )

    assert service.browse(SESSION).downloadable == frozenset()


# --- against a real crawl ----------------------------------------------------

TREE = {
    "/": f'<a href="/a">a</a><a href="{MEGA_LINK}">share</a>',
    "/a": "<p>leaf</p>",
}


def make_site() -> Site:
    """Return the local site the crawling tests here use."""
    site = Site()
    for path, markup in TREE.items():
        site.add_html(path, markup)
    return site


def make_settings(**settings: object) -> Settings:
    """Return throwaway settings that may reach the local test server."""
    settings.setdefault("allow_private_networks", True)
    return Settings(user_agent="MaxiCrawler/test", **settings)  # type: ignore[arg-type]


def test_what_a_crawl_wrote_is_what_comes_back(tmp_path: Path) -> None:
    settings = make_settings(database_path=tmp_path / "urls.db")
    crawls = CrawlService(settings)

    with serve(make_site()) as base:
        session = crawls.build_session(f"{base}/", depth=2, same_domain=True)
        report = crawls.run(session, persist=True)

    page = DiscoveryService(settings).browse(
        session.session_id, discovered=report.summary.unique_urls
    )

    assert page.recorded == report.summary.unique_urls
    assert MEGA_LINK in {item.raw_url for item in page.items}
    assert "mega" in {facet.value for facet in page.plugins}


def test_a_crawl_that_did_not_persist_recorded_no_urls(tmp_path: Path) -> None:
    """Which an interface must not confuse with a crawl that found none."""
    settings = make_settings(database_path=tmp_path / "urls.db")
    crawls = CrawlService(settings)

    with serve(make_site()) as base:
        session = crawls.build_session(f"{base}/", depth=2, same_domain=True)
        report = crawls.run(session, persist=False)

    page = DiscoveryService(settings).browse(
        session.session_id, discovered=report.summary.unique_urls
    )

    assert page.recorded == 0
    assert report.summary.unique_urls > 0
    assert page.was_recorded is False
