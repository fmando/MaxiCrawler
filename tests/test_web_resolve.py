"""Tests for base-URL handling and relative-URL resolution."""

import pytest

from maxicrawler.web import LinkKind, ParsedHtml, RawLink
from maxicrawler.web.resolve import is_http_url, resolve_base_url, resolve_link, resolve_links

PAGE = "https://example.test/docs/guide.html"


def link(value: str, kind: LinkKind = LinkKind.ANCHOR) -> RawLink:
    """Return a raw anchor link holding *value*."""
    return RawLink(value=value, kind=kind, tag="a", attribute="href")


# --- relative resolution -----------------------------------------------------


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("other.html", "https://example.test/docs/other.html"),
        ("./other.html", "https://example.test/docs/other.html"),
        ("../other.html", "https://example.test/other.html"),
        ("/root.html", "https://example.test/root.html"),
        ("?only=query", "https://example.test/docs/guide.html?only=query"),
        ("//other.test/x", "https://other.test/x"),
        ("https://other.test/x", "https://other.test/x"),
        ("http://other.test/x", "http://other.test/x"),
        ("sub/deep/page.html", "https://example.test/docs/sub/deep/page.html"),
    ],
)
def test_a_reference_is_resolved_against_the_page(reference: str, expected: str) -> None:
    assert resolve_link(PAGE, reference) == expected


def test_a_protocol_relative_reference_takes_the_pages_scheme() -> None:
    assert resolve_link("http://example.test/a", "//other.test/x") == "http://other.test/x"


@pytest.mark.parametrize(
    "reference",
    [
        "mailto:someone@example.test",
        "javascript:void(0)",
        "tel:+1234567890",
        "data:text/html,<b>x</b>",
        "ftp://example.test/file",
        "",
        "   ",
    ],
)
def test_a_reference_we_cannot_use_is_dropped(reference: str) -> None:
    assert resolve_link(PAGE, reference) is None


def test_a_same_document_reference_is_dropped() -> None:
    assert resolve_link(PAGE, "#section") is None


def test_a_surrounding_whitespace_is_ignored() -> None:
    assert resolve_link(PAGE, "  other.html  ") == "https://example.test/docs/other.html"


# --- fragments ---------------------------------------------------------------


def test_a_fragment_is_preserved() -> None:
    resolved = resolve_link(PAGE, "/page#section")

    assert resolved == "https://example.test/page#section"


def test_a_legacy_mega_link_keeps_its_key() -> None:
    """A legacy Mega share holds its whole identity in the fragment.

    Stripping it -- which is what a conventional crawler does -- would make
    unrelated shares compare equal and destroy a case-sensitive key, so the
    promise made by normalize_url has to hold here too.
    """
    key = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
    reference = f"https://mega.nz/#!AaBbCcDd!{key}"

    assert resolve_link(PAGE, reference) == reference


def test_a_relative_link_carrying_a_fragment_keeps_it() -> None:
    resolved = resolve_link(PAGE, "other.html#Section")

    assert resolved == "https://example.test/docs/other.html#Section"


def test_two_links_differing_only_in_the_fragment_stay_distinct() -> None:
    first = resolve_link(PAGE, "https://mega.nz/#!AaBbCcDd!key")
    second = resolve_link(PAGE, "https://mega.nz/#!ZzYyXxWw!key")

    assert first != second


# --- base URLs ---------------------------------------------------------------


def test_without_a_base_the_page_url_is_used() -> None:
    assert resolve_base_url(PAGE, None) == PAGE


def test_an_absolute_base_replaces_the_page_url() -> None:
    assert resolve_base_url(PAGE, "https://cdn.test/assets/") == "https://cdn.test/assets/"


def test_a_relative_base_is_resolved_against_the_page() -> None:
    assert resolve_base_url(PAGE, "../assets/") == "https://example.test/assets/"


def test_a_non_http_base_is_ignored() -> None:
    assert resolve_base_url(PAGE, "javascript:void(0)") == PAGE


def test_links_resolve_against_the_declared_base() -> None:
    parsed = ParsedHtml(base_href="https://cdn.test/assets/", raw_links=(link("logo.png"),))

    document = resolve_links(parsed, page_url=PAGE, encoding="utf-8")

    assert document.base_url == "https://cdn.test/assets/"
    assert document.links[0].resolved_url == "https://cdn.test/assets/logo.png"


def test_the_base_url_is_reported_even_without_a_base_element() -> None:
    document = resolve_links(ParsedHtml(), page_url=PAGE, encoding="utf-8")

    assert document.base_url == PAGE


# --- resolution against the final URL ----------------------------------------


def test_links_resolve_against_the_url_that_answered() -> None:
    """The page URL passed in is the final one, after redirects.

    A page reached through a redirect states its relative links against where
    it ended up; resolving against the requested URL is the classic crawler
    bug this pins down.
    """
    final = "https://www.example.test/new/section/"
    parsed = ParsedHtml(raw_links=(link("page.html"),))

    document = resolve_links(parsed, page_url=final, encoding="utf-8")

    assert document.links[0].resolved_url == "https://www.example.test/new/section/page.html"


# --- the document ------------------------------------------------------------


def test_every_dropped_reference_is_counted() -> None:
    parsed = ParsedHtml(
        raw_links=(
            link("/keep"),
            link("mailto:a@example.test"),
            link("#top"),
            link("javascript:void(0)"),
        )
    )

    document = resolve_links(parsed, page_url=PAGE, encoding="utf-8")

    assert len(document.links) == 1
    assert document.skipped_links == 3


def test_duplicates_are_kept_for_the_pipeline_to_count() -> None:
    parsed = ParsedHtml(raw_links=(link("/same"), link("/same")))

    document = resolve_links(parsed, page_url=PAGE, encoding="utf-8")

    assert len(document.links) == 2


def test_the_raw_reference_is_kept_beside_the_resolved_one() -> None:
    parsed = ParsedHtml(raw_links=(link("../up.html"),))

    (resolved,) = resolve_links(parsed, page_url=PAGE, encoding="utf-8").links

    assert resolved.raw_url == "../up.html"
    assert resolved.resolved_url == "https://example.test/up.html"


def test_the_kind_and_origin_of_a_link_survive_resolution() -> None:
    parsed = ParsedHtml(raw_links=(RawLink("/i.png", LinkKind.IMAGE, "img", "src"),))

    (resolved,) = resolve_links(parsed, page_url=PAGE, encoding="utf-8").links

    assert resolved.kind is LinkKind.IMAGE
    assert resolved.tag == "img"
    assert resolved.attribute == "src"


def test_extra_links_pass_through_the_same_resolution() -> None:
    parsed = ParsedHtml(raw_links=(link("/markup"),))
    prose = (RawLink("https://example.test/prose", LinkKind.TEXT, "", ""),)

    document = resolve_links(parsed, page_url=PAGE, encoding="utf-8", extra=prose)

    assert [item.resolved_url for item in document.links] == [
        "https://example.test/markup",
        "https://example.test/prose",
    ]


def test_the_canonical_url_is_resolved() -> None:
    parsed = ParsedHtml(canonical_href="/real")

    document = resolve_links(parsed, page_url=PAGE, encoding="utf-8")

    assert document.canonical_url == "https://example.test/real"


def test_no_canonical_reports_none() -> None:
    document = resolve_links(ParsedHtml(), page_url=PAGE, encoding="utf-8")

    assert document.canonical_url is None


def test_the_title_and_encoding_are_carried_over() -> None:
    parsed = ParsedHtml(title="Guide")

    document = resolve_links(parsed, page_url=PAGE, encoding="iso8859-1")

    assert document.title == "Guide"
    assert document.encoding == "iso8859-1"
    assert document.url == PAGE


def test_truncation_is_carried_over() -> None:
    document = resolve_links(ParsedHtml(truncated=True), page_url=PAGE, encoding="utf-8")

    assert document.truncated is True


# --- the predicate -----------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.test/", True),
        ("http://example.test", True),
        ("HTTPS://Example.TEST/", True),
        ("ftp://example.test/", False),
        ("https:///nohost", False),
        ("/relative", False),
        ("", False),
    ],
)
def test_is_http_url(url: str, expected: bool) -> None:
    assert is_http_url(url) is expected
