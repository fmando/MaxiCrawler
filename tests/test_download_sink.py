"""Tests for the sink that writes provider content into the library."""

from hashlib import sha256
from pathlib import Path

import pytest
from doubles import make_ref

from maxicrawler.domain import ContentDescriptor
from maxicrawler.downloader import DownloadError, LibrarySink
from maxicrawler.library import CONTENT_DIRECTORY, Library, LibraryEntry

PAYLOAD = b"ubuntu release image, in miniature"


def make_entry(tmp_path: Path) -> LibraryEntry:
    """Return an entry inside an initialized library."""
    library = Library(tmp_path / "library")
    library.initialize()
    return library.entry(make_ref())


def test_a_payload_is_stored_and_described(tmp_path: Path) -> None:
    entry = make_entry(tmp_path)

    with LibrarySink(entry) as sink:
        sink.begin(ContentDescriptor(name="ubuntu.iso", size=len(PAYLOAD)))
        sink.write(PAYLOAD)
        content = sink.commit()

    assert content.filename == "ubuntu.iso"
    assert content.path == f"{CONTENT_DIRECTORY}/ubuntu.iso"
    assert content.size == len(PAYLOAD)
    assert (entry.path / content.path).read_bytes() == PAYLOAD


def test_the_digest_is_of_what_was_written(tmp_path: Path) -> None:
    entry = make_entry(tmp_path)

    with LibrarySink(entry) as sink:
        sink.begin(ContentDescriptor(name="ubuntu.iso", size=len(PAYLOAD)))
        for start in range(0, len(PAYLOAD), 5):
            sink.write(PAYLOAD[start : start + 5])
        content = sink.commit()

    assert content.checksum("sha256") == sha256(PAYLOAD).hexdigest()


def test_progress_reports_the_running_total(tmp_path: Path) -> None:
    entry = make_entry(tmp_path)
    seen: list[int] = []

    with LibrarySink(entry, on_progress=seen.append) as sink:
        sink.begin(ContentDescriptor(name="ubuntu.iso", size=6))
        sink.write(b"abc")
        sink.write(b"def")
        sink.commit()

    assert seen == [3, 6]


def test_nothing_is_visible_before_the_payload_is_committed(tmp_path: Path) -> None:
    entry = make_entry(tmp_path)

    sink = LibrarySink(entry)
    sink.begin(ContentDescriptor(name="ubuntu.iso", size=len(PAYLOAD)))
    sink.write(PAYLOAD)

    assert not entry.content_directory.exists()
    sink.commit()
    assert (entry.content_directory / "ubuntu.iso").exists()


def test_an_abandoned_transfer_leaves_the_library_untouched(tmp_path: Path) -> None:
    entry = make_entry(tmp_path)

    with (
        pytest.raises(RuntimeError, match="the network went away"),
        LibrarySink(entry) as sink,
    ):
        sink.begin(ContentDescriptor(name="ubuntu.iso", size=len(PAYLOAD)))
        sink.write(PAYLOAD[:10])
        msg = "the network went away"
        raise RuntimeError(msg)

    assert not entry.content_directory.exists()
    assert not entry.staging_directory.exists()


def test_a_short_payload_is_refused_rather_than_stored(tmp_path: Path) -> None:
    entry = make_entry(tmp_path)

    with LibrarySink(entry) as sink:
        sink.begin(ContentDescriptor(name="ubuntu.iso", size=len(PAYLOAD)))
        sink.write(PAYLOAD[:10])

        with pytest.raises(DownloadError, match=f"incomplete: 10 of {len(PAYLOAD)} bytes"):
            sink.commit()

    assert not entry.content_directory.exists()


def test_a_payload_of_unknown_size_is_accepted(tmp_path: Path) -> None:
    entry = make_entry(tmp_path)

    with LibrarySink(entry) as sink:
        sink.begin(ContentDescriptor(name="notes.txt"))
        sink.write(PAYLOAD)
        content = sink.commit()

    assert content.size == len(PAYLOAD)


def test_an_empty_payload_is_stored(tmp_path: Path) -> None:
    entry = make_entry(tmp_path)

    with LibrarySink(entry) as sink:
        sink.begin(ContentDescriptor(name="empty.bin", size=0))
        content = sink.commit()

    assert content.size == 0
    assert (entry.path / content.path).read_bytes() == b""


def test_a_nameless_payload_falls_back(tmp_path: Path) -> None:
    entry = make_entry(tmp_path)

    with LibrarySink(entry) as sink:
        sink.begin(ContentDescriptor(name=None, size=len(PAYLOAD)))
        sink.write(PAYLOAD)
        content = sink.commit()

    assert content.filename == "content.bin"


def test_a_hostile_name_cannot_escape_the_entry(tmp_path: Path) -> None:
    entry = make_entry(tmp_path)

    with LibrarySink(entry) as sink:
        sink.begin(ContentDescriptor(name="../../escaped.iso", size=len(PAYLOAD)))
        sink.write(PAYLOAD)
        content = sink.commit()

    assert (entry.path / content.path).parent == entry.content_directory
    assert not (tmp_path / "escaped.iso").exists()


def test_writing_before_the_content_is_announced_is_refused(tmp_path: Path) -> None:
    with (
        LibrarySink(make_entry(tmp_path)) as sink,
        pytest.raises(DownloadError, match="before the transfer announced it"),
    ):
        sink.write(PAYLOAD)


def test_announcing_content_twice_is_refused(tmp_path: Path) -> None:
    with LibrarySink(make_entry(tmp_path)) as sink:
        sink.begin(ContentDescriptor(name="a.bin"))

        with pytest.raises(DownloadError, match="announced its content twice"):
            sink.begin(ContentDescriptor(name="b.bin"))


def test_committing_without_content_is_refused(tmp_path: Path) -> None:
    with (
        LibrarySink(make_entry(tmp_path)) as sink,
        pytest.raises(DownloadError, match="announced no content"),
    ):
        sink.commit()


def test_the_sink_reports_what_it_knows(tmp_path: Path) -> None:
    with LibrarySink(make_entry(tmp_path)) as sink:
        sink.begin(ContentDescriptor(name="ubuntu.iso", size=99))
        sink.write(PAYLOAD)

        assert sink.filename == "ubuntu.iso"
        assert sink.bytes_written == len(PAYLOAD)
        assert sink.expected_size == 99
