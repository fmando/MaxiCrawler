"""Tests for the document reader layer."""

from pathlib import Path

import pytest

from maxicrawler.documents import (
    Document,
    DocumentLoader,
    DocumentReader,
    DocumentType,
    HtmlDocumentReader,
    MarkdownDocumentReader,
    TextDocumentReader,
    UnsupportedDocumentError,
)

DATA = Path(__file__).parent / "data" / "documents"


def test_readers_implement_the_document_reader_protocol() -> None:
    for reader in (TextDocumentReader(), MarkdownDocumentReader(), HtmlDocumentReader()):
        assert isinstance(reader, DocumentReader)


def test_text_reader_returns_the_file_verbatim() -> None:
    document = TextDocumentReader().read(DATA / "release-notes.txt")

    assert document.document_type is DocumentType.TEXT
    assert document.links == ()
    assert "https://example.test/changelog" in document.text
    assert document.text == (DATA / "release-notes.txt").read_text(encoding="utf-8")


def test_markdown_reader_keeps_link_syntax_in_the_text() -> None:
    document = MarkdownDocumentReader().read(DATA / "reading-list.md")

    assert document.document_type is DocumentType.MARKDOWN
    assert document.links == ()
    assert "[URL Living Standard](https://spec.example.test/url)" in document.text
    assert "<https://blog.example.test/parsing-urls>" in document.text


def test_html_reader_separates_markup_links_from_prose() -> None:
    document = HtmlDocumentReader().read(DATA / "index.html")

    assert document.document_type is DocumentType.HTML
    assert "https://docs.example.test/getting-started" in document.links
    assert "https://cdn.example.test/site.css" in document.links
    assert "https://cdn.example.test/logo.png" in document.links
    assert "/relative/page" in document.links
    assert "Getting started" in document.text


def test_html_reader_resolves_character_references_in_attributes() -> None:
    document = HtmlDocumentReader().read(DATA / "index.html")

    assert "https://docs.example.test/search?q=urls&lang=en" in document.links


def test_html_reader_ignores_script_and_style_content() -> None:
    document = HtmlDocumentReader().read(DATA / "index.html")

    assert "not-a-real-link-in-script" not in document.text
    assert "not-a-real-link-in-css" not in document.text


def test_html_reader_tolerates_malformed_markup() -> None:
    document = HtmlDocumentReader().read(DATA / "index.html")

    assert "http://legacy.example.test/archive" in document.text


def test_document_source_is_platform_independent(tmp_path: Path) -> None:
    document = Document(
        path=tmp_path / "sub" / "file.txt", document_type=DocumentType.TEXT, text=""
    )

    assert "\\" not in document.source
    assert document.source.endswith("sub/file.txt")


def test_reader_replaces_undecodable_bytes(tmp_path: Path) -> None:
    path = tmp_path / "broken.txt"
    path.write_bytes(b"before \xff\xfe after https://example.test/ok")

    document = TextDocumentReader().read(path)

    assert "https://example.test/ok" in document.text


def test_loader_selects_a_reader_by_suffix() -> None:
    loader = DocumentLoader()

    assert loader.supports(Path("a.txt")) is True
    assert loader.supports(Path("a.md")) is True
    assert loader.supports(Path("a.html")) is True
    assert loader.supports(Path("a.htm")) is True
    assert loader.supports(Path("a.json")) is False
    assert loader.supported_suffixes == frozenset({".txt", ".md", ".html", ".htm"})


def test_loader_suffix_matching_is_case_insensitive(tmp_path: Path) -> None:
    path = tmp_path / "SHOUTING.HTML"
    path.write_text("<a href='https://example.test/a'>a</a>", encoding="utf-8")

    assert DocumentLoader().read(path).document_type is DocumentType.HTML


def test_loader_rejects_unsupported_files() -> None:
    with pytest.raises(UnsupportedDocumentError):
        DocumentLoader().read(DATA / "nested" / "config.json")


def test_loader_walks_a_directory_recursively_and_deterministically() -> None:
    paths = list(DocumentLoader().iter_paths(DATA))

    assert [path.name for path in paths] == [
        "index.html",
        "notes.md",
        "reading-list.md",
        "release-notes.txt",
    ]


def test_loader_accepts_a_single_file() -> None:
    paths = list(DocumentLoader().iter_paths(DATA / "release-notes.txt"))

    assert [path.name for path in paths] == ["release-notes.txt"]


def test_loader_yields_nothing_for_a_single_unsupported_file() -> None:
    assert list(DocumentLoader().iter_paths(DATA / "nested" / "config.json")) == []


def test_loader_skips_hidden_directories(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hidden.txt").write_text("https://example.test/hidden", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("https://example.test/visible", encoding="utf-8")

    paths = list(DocumentLoader().iter_paths(tmp_path))

    assert [path.name for path in paths] == ["visible.txt"]


def test_loader_raises_for_a_missing_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list(DocumentLoader().iter_paths(tmp_path / "missing"))


def test_loader_load_all_reads_every_supported_document() -> None:
    documents = list(DocumentLoader().load_all(DATA))

    assert [document.document_type for document in documents] == [
        DocumentType.HTML,
        DocumentType.MARKDOWN,
        DocumentType.MARKDOWN,
        DocumentType.TEXT,
    ]


def test_loader_accepts_a_custom_reader_set() -> None:
    loader = DocumentLoader([TextDocumentReader()])

    assert loader.supported_suffixes == frozenset({".txt"})
    assert loader.readers == (loader.readers[0],)
    assert loader.supports(Path("a.md")) is False
