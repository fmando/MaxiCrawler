"""Tests for the generic URL extractor."""

from pathlib import Path

import pytest

from maxicrawler.documents import Document, DocumentLoader, DocumentType
from maxicrawler.extractors import GenericUrlExtractor, UrlExtractor

DATA = Path(__file__).parent / "data" / "documents"


def make_document(text: str = "", links: tuple[str, ...] = ()) -> Document:
    """Return an in-memory document without touching the file system."""
    return Document(
        path=Path("memory.txt"), document_type=DocumentType.TEXT, text=text, links=links
    )


def extract(text: str = "", links: tuple[str, ...] = ()) -> list[str]:
    """Return the raw URLs extracted from an in-memory document."""
    return [
        candidate.raw_url for candidate in GenericUrlExtractor().extract(make_document(text, links))
    ]


def test_generic_extractor_implements_the_protocol() -> None:
    assert isinstance(GenericUrlExtractor(), UrlExtractor)


def test_extracts_bare_urls_from_prose() -> None:
    assert extract("See https://example.test/a and http://example.test/b today.") == [
        "https://example.test/a",
        "http://example.test/b",
    ]


def test_preserves_the_original_url() -> None:
    candidates = GenericUrlExtractor().extract(
        make_document("HTTPS://EXAMPLE.test:443/Docs?b=2&a=1")
    )

    assert candidates[0].raw_url == "HTTPS://EXAMPLE.test:443/Docs?b=2&a=1"
    assert candidates[0].normalized_url == "https://example.test/Docs?a=1&b=2"


def test_strips_trailing_prose_punctuation() -> None:
    assert extract("Read https://example.test/a. Then https://example.test/b, please!") == [
        "https://example.test/a",
        "https://example.test/b",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "no urls at all",
        "mailto:user@example.test",
        "ftp://legacy.example.test/file",
        "https:// is not a url",
        "just a bare word: https",
        "/relative/path",
    ],
)
def test_ignores_malformed_and_unsupported_urls(text: str) -> None:
    assert extract(text) == []


def test_ignores_malformed_markup_links() -> None:
    assert extract(links=("/relative/page", "mailto:docs@example.test", "")) == []


def test_removes_duplicates_within_one_document() -> None:
    assert extract(
        "https://example.test/a and again https://example.test/a",
        links=("https://example.test/a",),
    ) == ["https://example.test/a"]


def test_deduplication_uses_the_normalized_form() -> None:
    assert extract("https://EXAMPLE.test:443/docs?b=2&a=1 https://example.test/docs?a=1&b=2") == [
        "https://EXAMPLE.test:443/docs?b=2&a=1"
    ]


def test_markup_links_are_reported_before_prose() -> None:
    assert extract("text https://example.test/prose", links=("https://example.test/markup",)) == [
        "https://example.test/markup",
        "https://example.test/prose",
    ]


def test_markdown_inline_link_syntax_is_not_swallowed() -> None:
    assert extract("A [link](https://example.test/a) in a sentence.") == ["https://example.test/a"]


def test_markdown_autolink_syntax_is_not_swallowed() -> None:
    assert extract("An autolink <https://example.test/a> here.") == ["https://example.test/a"]


def test_extracts_from_the_sample_text_document() -> None:
    document = DocumentLoader().read(DATA / "release-notes.txt")

    urls = [candidate.normalized_url for candidate in GenericUrlExtractor().extract(document)]

    assert urls == [
        "https://example.test/changelog",
        "https://example.test/docs/migration",
        "https://example.test/issues?state=open",
        "https://example.test/support",
        "http://mirror.example.test/releases/",
    ]


def test_extracts_from_the_sample_markdown_document() -> None:
    document = DocumentLoader().read(DATA / "reading-list.md")

    urls = [candidate.normalized_url for candidate in GenericUrlExtractor().extract(document)]

    assert urls == [
        "https://spec.example.test/url",
        "https://spec.example.test/robots",
        "https://blog.example.test/parsing-urls",
        "https://blog.example.test/normalization",
        "https://archive.example.test/papers",
        "http://mirror.example.test/papers",
        "https://example.test/in-code-span",
    ]


def test_extracts_from_the_sample_html_document() -> None:
    document = DocumentLoader().read(DATA / "index.html")

    urls = [candidate.normalized_url for candidate in GenericUrlExtractor().extract(document)]

    assert urls == [
        "https://cdn.example.test/site.css",
        "https://cdn.example.test/analytics.js",
        "https://docs.example.test/getting-started",
        "https://docs.example.test/plugins",
        "https://docs.example.test/search?lang=en&q=urls",
        "https://docs.example.test/",
        "https://cdn.example.test/logo.png",
        "http://legacy.example.test/archive",
    ]


def test_script_and_style_urls_are_not_extracted() -> None:
    document = DocumentLoader().read(DATA / "index.html")

    urls = [candidate.normalized_url for candidate in GenericUrlExtractor().extract(document)]

    assert not any("not-a-real-link" in url for url in urls)


def test_extraction_of_an_empty_document_returns_an_empty_tuple() -> None:
    assert GenericUrlExtractor().extract(make_document()) == ()
