"""Tests for deciding what a download source actually is."""

from pathlib import Path

import pytest

from maxicrawler.downloader import SourceError, SourceResolver, looks_like_url

DOCUMENTS = Path(__file__).parent / "data" / "documents"
MEGA = Path(__file__).parent / "data" / "mega"


@pytest.mark.parametrize(
    "value",
    [
        "https://mega.nz/file/AaBbCcDd#key",
        "http://example.test/a",
        "  https://example.test/a  ",
    ],
)
def test_a_url_is_recognized(value: str) -> None:
    assert looks_like_url(value) is True


@pytest.mark.parametrize(
    "value",
    ["links.txt", "./docs", "C:\\Users\\me\\links.txt", "/home/me/links.txt", "", "ftp://x/y"],
)
def test_a_path_is_not_mistaken_for_a_url(value: str) -> None:
    assert looks_like_url(value) is False


def test_a_url_stands_for_itself() -> None:
    url = "https://mega.nz/file/AaBbCcDd#0123456789"

    items = SourceResolver().resolve(url)

    assert len(items) == 1
    assert items[0].url == url
    assert items[0].origin is None


def test_a_document_yields_the_urls_it_contains() -> None:
    items = SourceResolver().resolve(str(DOCUMENTS / "release-notes.txt"))

    assert len(items) == 5
    assert all(item.origin is not None for item in items)


def test_a_directory_is_read_recursively() -> None:
    items = SourceResolver().resolve(str(DOCUMENTS))

    assert len(items) == 21
    assert len({item.origin for item in items}) > 1


def test_a_url_written_in_two_documents_is_downloaded_once() -> None:
    items = SourceResolver().resolve(str(MEGA))

    assert len({item.url for item in items}) == len(items)


def test_a_directory_without_documents_yields_nothing(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    assert SourceResolver().resolve(str(empty)) == ()


def test_a_missing_path_names_both_interpretations(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="neither an HTTP\\(S\\) URL nor an existing path"):
        SourceResolver().resolve(str(tmp_path / "nowhere.txt"))


def test_an_unreadable_document_type_is_reported(tmp_path: Path) -> None:
    archive = tmp_path / "links.pdf"
    archive.write_bytes(b"%PDF-1.4")

    with pytest.raises(SourceError, match="unsupported document type"):
        SourceResolver().resolve(str(archive))


def test_an_empty_source_is_reported() -> None:
    with pytest.raises(SourceError, match="no download source was given"):
        SourceResolver().resolve("   ")
