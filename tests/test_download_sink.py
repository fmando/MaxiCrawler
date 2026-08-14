"""Tests for the sink that writes provider content into the library."""

from hashlib import sha256
from pathlib import Path

import pytest
from doubles import make_ref

from maxicrawler.domain import ContentDescriptor
from maxicrawler.downloader import (
    DownloadCancelledError,
    DownloadControl,
    DownloadError,
    DownloadRefusedError,
    LibrarySink,
)
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


def test_a_finished_entry_keeps_no_staging_directory(tmp_path: Path) -> None:
    entry = make_entry(tmp_path)

    with LibrarySink(entry) as sink:
        sink.begin(ContentDescriptor(name="ubuntu.iso", size=len(PAYLOAD)))
        sink.write(PAYLOAD)
        sink.commit()

    assert not entry.staging_directory.exists()
    assert sorted(path.name for path in entry.path.iterdir()) == ["content"]


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


def test_an_announced_payload_under_the_floor_is_never_transferred(tmp_path: Path) -> None:
    entry = make_entry(tmp_path)

    with (
        pytest.raises(DownloadRefusedError, match="under the minimum download size"),
        LibrarySink(entry, minimum_size=1000) as sink,
    ):
        sink.begin(ContentDescriptor(name="thumb.jpg", size=120))

    assert not entry.staging_directory.exists()
    assert not entry.content_directory.exists()


def test_a_refusal_names_both_sizes(tmp_path: Path) -> None:
    """A file that vanished for a reason nobody can check is the failure here."""
    entry = make_entry(tmp_path)

    with (
        pytest.raises(DownloadRefusedError, match=r"120 B of 1.0 KB"),
        LibrarySink(entry, minimum_size=1000) as sink,
    ):
        sink.begin(ContentDescriptor(name="thumb.jpg", size=120))


def test_a_payload_that_turns_out_small_is_caught_at_the_end(tmp_path: Path) -> None:
    """The case an announced size cannot cover: a host that stated no length."""
    entry = make_entry(tmp_path)

    with (
        pytest.raises(DownloadRefusedError, match="under the minimum download size"),
        LibrarySink(entry, minimum_size=1000) as sink,
    ):
        sink.begin(ContentDescriptor(name="thumb.jpg"))
        sink.write(b"tiny")
        sink.commit()

    assert not entry.staging_directory.exists()
    assert not entry.content_directory.exists()


def test_a_payload_at_the_floor_is_kept(tmp_path: Path) -> None:
    """The limit is a minimum, so the size it names is large enough."""
    entry = make_entry(tmp_path)

    with LibrarySink(entry, minimum_size=len(PAYLOAD)) as sink:
        sink.begin(ContentDescriptor(name="ubuntu.iso", size=len(PAYLOAD)))
        sink.write(PAYLOAD)
        content = sink.commit()

    assert content.size == len(PAYLOAD)


def test_an_unknown_size_is_not_a_small_one(tmp_path: Path) -> None:
    """`None` means the host stated no length, which is not a reason to refuse."""
    entry = make_entry(tmp_path)

    with LibrarySink(entry, minimum_size=len(PAYLOAD)) as sink:
        sink.begin(ContentDescriptor(name="ubuntu.iso"))
        sink.write(PAYLOAD)

        assert sink.commit().size == len(PAYLOAD)


def test_a_floor_of_zero_keeps_everything(tmp_path: Path) -> None:
    entry = make_entry(tmp_path)

    with LibrarySink(entry, minimum_size=0) as sink:
        sink.begin(ContentDescriptor(name="pixel.gif", size=1))
        sink.write(b"x")

        assert sink.commit().size == 1


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


# --- stopping a transfer -----------------------------------------------------


def test_a_stop_ends_the_transfer_at_the_next_chunk(tmp_path: Path) -> None:
    entry = make_entry(tmp_path)
    control = DownloadControl()

    with pytest.raises(DownloadCancelledError), LibrarySink(entry, control=control) as sink:
        sink.begin(ContentDescriptor(name="ubuntu.iso", size=len(PAYLOAD)))
        sink.write(b"first half ")
        control.request_stop()
        sink.write(b"second half")


def test_a_stopped_transfer_leaves_the_library_as_it_was(tmp_path: Path) -> None:
    """The property that makes cancelling safe, and it is not new.

    A transfer that stops is discarded by the same context manager that
    discards one that broke, so nothing half-written was ever visible.
    """
    entry = make_entry(tmp_path)
    control = DownloadControl()
    control.request_stop()

    with pytest.raises(DownloadCancelledError), LibrarySink(entry, control=control) as sink:
        sink.begin(ContentDescriptor(name="ubuntu.iso", size=len(PAYLOAD)))
        sink.write(PAYLOAD)

    assert not (entry.path / CONTENT_DIRECTORY).exists()
    assert not entry.staging_directory.exists()


def test_a_stop_says_how_far_it_got(tmp_path: Path) -> None:
    entry = make_entry(tmp_path)
    control = DownloadControl()

    with (
        pytest.raises(DownloadCancelledError, match="11 bytes"),
        LibrarySink(entry, control=control) as sink,
    ):
        sink.begin(ContentDescriptor(name="ubuntu.iso", size=len(PAYLOAD)))
        sink.write(b"first half ")
        control.request_stop()
        sink.write(b"second half")


def test_a_sink_without_a_control_is_unchanged(tmp_path: Path) -> None:
    entry = make_entry(tmp_path)

    with LibrarySink(entry) as sink:
        sink.begin(ContentDescriptor(name="ubuntu.iso", size=len(PAYLOAD)))
        sink.write(PAYLOAD)
        content = sink.commit()

    assert content.size == len(PAYLOAD)


def test_a_control_nobody_pressed_changes_nothing(tmp_path: Path) -> None:
    entry = make_entry(tmp_path)

    with LibrarySink(entry, control=DownloadControl()) as sink:
        sink.begin(ContentDescriptor(name="ubuntu.iso", size=len(PAYLOAD)))
        sink.write(PAYLOAD)
        content = sink.commit()

    assert (entry.path / content.path).read_bytes() == PAYLOAD
