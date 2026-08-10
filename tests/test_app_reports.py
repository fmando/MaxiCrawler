"""Tests for reading the pages one crawl reached.

Pure functions over a value, so nothing here crawls anything.
"""

import pytest

from maxicrawler.app import PageQuery, PageState, browse_pages, count_pages
from maxicrawler.web.report import PageOutcome


def read(url: str, *, title: str | None = None, final_url: str | None = None) -> PageOutcome:
    """Return a page that was fetched and read."""
    return PageOutcome(
        url=url, depth=1, status=200, title=title, final_url=final_url if final_url else url
    )


def failed(url: str, *, error: str = "HTTP 404") -> PageOutcome:
    """Return a page that could not be read."""
    return PageOutcome(url=url, depth=1, error=error)


def urls_of(slice_: object) -> list[str]:
    """Return the URLs a slice lists, in the order it lists them."""
    return [page.url for page in slice_.items]  # type: ignore[attr-defined]


SITE = (
    read("https://example.test/"),
    read("https://example.test/a", title="Holiday"),
    failed("https://example.test/gone"),
    read("https://example.test/old", final_url="https://example.test/new"),
    read("https://example.test/b"),
)


# --- filtering ---------------------------------------------------------------


def test_everything_is_shown_by_default() -> None:
    result = browse_pages(SITE)

    assert result.total == 5
    assert result.recorded == 5
    assert result.query.is_filtered is False


def test_only_the_failures() -> None:
    result = browse_pages(SITE, PageQuery(state=PageState.FAILED))

    assert urls_of(result) == ["https://example.test/gone"]


def test_only_the_pages_that_were_read() -> None:
    result = browse_pages(SITE, PageQuery(state=PageState.SUCCEEDED))

    assert "https://example.test/gone" not in urls_of(result)
    assert result.total == 4


def test_only_the_ones_that_redirected() -> None:
    result = browse_pages(SITE, PageQuery(state=PageState.REDIRECTED))

    assert urls_of(result) == ["https://example.test/old"]


def test_a_redirect_is_a_separate_fact_from_how_the_page_went() -> None:
    """It answers a different question, which is why one selector covers three."""
    result = browse_pages(SITE, PageQuery(state=PageState.SUCCEEDED))

    assert "https://example.test/old" in urls_of(result)


def test_searching_matches_the_url_the_answer_and_the_title() -> None:
    assert browse_pages(SITE, PageQuery(search="HOLIDAY")).total == 1
    assert browse_pages(SITE, PageQuery(search="/new")).total == 1
    assert browse_pages(SITE, PageQuery(search="gone")).total == 1


def test_a_search_and_a_state_combine() -> None:
    result = browse_pages(SITE, PageQuery(search="example", state=PageState.FAILED))

    assert urls_of(result) == ["https://example.test/gone"]


def test_a_filtered_query_says_so() -> None:
    assert PageQuery().is_filtered is False
    assert PageQuery(search="a").is_filtered is True
    assert PageQuery(state=PageState.FAILED).is_filtered is True


@pytest.mark.parametrize("value", ["", None, "sideways", "SUCCEEDED"])
def test_a_state_nobody_recognises_filters_nothing(value: str | None) -> None:
    """It arrives in a query string, where a stale bookmark is ordinary."""
    assert PageState.parse(value) is None


def test_a_state_that_is_recognised_is_read() -> None:
    assert PageState.parse("failed") is PageState.FAILED


# --- counting ----------------------------------------------------------------


def test_the_counts_cover_every_page_rather_than_the_matches() -> None:
    """Choosing a filter must not remove the entry you would pick instead."""
    result = browse_pages(SITE, PageQuery(state=PageState.FAILED))

    assert result.counts.succeeded == 4
    assert result.counts.failed == 1
    assert result.counts.redirected == 1


def test_a_count_is_asked_for_by_its_state() -> None:
    counts = count_pages(SITE)

    assert counts.of(PageState.SUCCEEDED) == 4
    assert counts.of(PageState.FAILED) == 1
    assert counts.of(PageState.REDIRECTED) == 1


def test_no_pages_count_to_nothing() -> None:
    counts = count_pages(())

    assert (counts.succeeded, counts.failed, counts.redirected) == (0, 0, 0)


# --- paging ------------------------------------------------------------------


def make_pages(count: int) -> tuple[PageOutcome, ...]:
    """Return *count* pages that were read, in the order they were reached."""
    return tuple(read(f"https://example.test/{index}") for index in range(count))


def test_a_slice_states_what_it_left_out() -> None:
    result = browse_pages(make_pages(9), PageQuery(per_page=4))

    assert len(result.items) == 4
    assert result.hidden == 5
    assert result.pages == 3
    assert (result.first, result.last) == (1, 4)
    assert result.has_next is True
    assert result.has_previous is False


def test_the_second_page_continues_where_the_first_stopped() -> None:
    result = browse_pages(make_pages(9), PageQuery(per_page=4, page=2))

    assert urls_of(result) == [f"https://example.test/{index}" for index in range(4, 8)]
    assert (result.first, result.last) == (5, 8)
    assert result.has_previous is True


def test_a_page_number_past_the_end_answers_with_the_last_one() -> None:
    result = browse_pages(make_pages(9), PageQuery(per_page=4, page=99))

    assert result.page == 3
    assert len(result.items) == 1


def test_an_unreasonable_page_size_is_capped() -> None:
    result = browse_pages(make_pages(3), PageQuery(per_page=10_000_000))

    assert len(result.items) == 3
    assert result.pages == 1


def test_the_order_a_crawl_walked_a_site_in_is_never_changed() -> None:
    """It is how a reader sees that the failures all began after page twenty."""
    result = browse_pages(SITE)

    assert urls_of(result) == [page.url for page in SITE]


def test_a_crawl_that_reached_nothing_is_an_empty_slice() -> None:
    result = browse_pages(())

    assert result.items == ()
    assert result.recorded == 0
    assert result.pages == 1
    assert (result.first, result.last) == (0, 0)
