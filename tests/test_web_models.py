"""Tests for the value objects of the web layer."""

from datetime import UTC, datetime

import pytest

from maxicrawler.crawler import DiscoverySummary, PluginUsage
from maxicrawler.domain import ScanSession, Statistics
from maxicrawler.web import (
    CrawlResult,
    FetchedPage,
    HtmlDocument,
    LinkKind,
    PageInfo,
    PageLink,
)


def make_summary() -> DiscoverySummary:
    """Return a summary with one document and two unique URLs."""
    session = ScanSession(session_id="s1", started_at=datetime(2026, 8, 6, tzinfo=UTC))
    statistics = Statistics(documents_processed=1, discovered_urls=2, duplicate_urls=1)
    return DiscoverySummary(
        session=session,
        statistics=statistics,
        plugin_usage=(PluginUsage(name="generic", count=2),),
    )


def make_link(url: str, kind: LinkKind = LinkKind.ANCHOR) -> PageLink:
    """Return a resolved link of *kind* pointing at *url*."""
    return PageLink(raw_url=url, resolved_url=url, kind=kind, tag="a", attribute="href")


def make_result(
    *,
    requested_url: str = "https://example.test/",
    final_url: str = "https://example.test/",
    redirects: tuple[str, ...] = (),
    links: tuple[PageLink, ...] = (),
    skipped_links: int = 0,
) -> CrawlResult:
    """Return a crawl result over the given retrieval and links."""
    page = PageInfo(
        requested_url=requested_url,
        final_url=final_url,
        status=200,
        size=42,
        encoding="utf-8",
        content_type="text/html",
        redirects=redirects,
    )
    document = HtmlDocument(
        url=final_url,
        base_url=final_url,
        encoding="utf-8",
        links=links,
        skipped_links=skipped_links,
    )
    return CrawlResult(page=page, document=document, summary=make_summary())


def test_crawl_result_preserves_the_requested_url() -> None:
    result = make_result(
        requested_url="http://example.test/start",
        final_url="https://www.example.test/end",
        redirects=("https://www.example.test/end",),
    )

    assert result.requested_url == "http://example.test/start"


def test_crawl_result_preserves_the_final_url() -> None:
    result = make_result(
        requested_url="http://example.test/start",
        final_url="https://www.example.test/end",
        redirects=("https://www.example.test/end",),
    )

    assert result.final_url == "https://www.example.test/end"


def test_crawl_result_keeps_both_urls_distinguishable_after_a_redirect() -> None:
    result = make_result(
        requested_url="http://example.test/start",
        final_url="https://www.example.test/end",
        redirects=("https://www.example.test/end",),
    )

    assert result.requested_url != result.final_url
    assert result.was_redirected is True
    assert result.redirects == ("https://www.example.test/end",)


def test_crawl_result_reports_no_redirect_when_the_urls_agree() -> None:
    result = make_result()

    assert result.requested_url == result.final_url
    assert result.was_redirected is False


def test_crawl_result_is_immutable() -> None:
    result = make_result()

    with pytest.raises(AttributeError):
        result.page = result.page  # type: ignore[misc]


def test_crawl_result_counts_links_by_kind_dropping_empty_kinds() -> None:
    result = make_result(
        links=(
            make_link("https://example.test/a"),
            make_link("https://example.test/b"),
            make_link("https://example.test/c.png", LinkKind.IMAGE),
        )
    )

    assert result.links_by_kind() == {LinkKind.ANCHOR: 2, LinkKind.IMAGE: 1}


def test_crawl_result_counts_every_link_including_repeats() -> None:
    result = make_result(
        links=(make_link("https://example.test/a"), make_link("https://example.test/a")),
        skipped_links=3,
    )

    assert result.link_count == 2
    assert result.skipped_links == 3


def test_crawl_result_exposes_the_reused_discovery_summary() -> None:
    result = make_result()

    assert result.summary.documents_processed == 1
    assert result.summary.unique_urls == 2
    assert result.summary.duplicates_removed == 1
    assert result.summary.plugin_usage[0].name == "generic"


def test_page_info_drops_the_body_of_a_fetched_page() -> None:
    page = FetchedPage(
        requested_url="https://example.test/",
        final_url="https://example.test/",
        status=200,
        body=b"<html></html>",
        content_type="text/html",
        content_encoding="gzip",
        redirects=(),
    )

    info = PageInfo.of(page, encoding="utf-8", size=len(page.body))

    assert not hasattr(info, "body")
    assert info.size == 13
    assert info.encoding == "utf-8"
    assert info.content_encoding == "gzip"
    assert info.requested_url == page.requested_url
    assert info.final_url == page.final_url


def test_fetched_page_reports_whether_it_was_redirected() -> None:
    page = FetchedPage(
        requested_url="https://example.test/",
        final_url="https://example.test/moved",
        status=200,
        body=b"",
        redirects=("https://example.test/moved",),
    )

    assert page.was_redirected is True
