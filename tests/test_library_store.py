"""Tests for the on-disk library layout."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from doubles import make_ref

from maxicrawler.domain import DownloadStatus
from maxicrawler.library import (
    CONTENT_DIRECTORY,
    DESCRIPTOR_FILENAME,
    LIBRARY_SCHEMA,
    METADATA_FILENAME,
    STAGING_DIRECTORY,
    ContentRecord,
    Library,
    LibraryEntry,
    LibraryError,
    LibraryRecordError,
    new_record,
)


def make_library(tmp_path: Path) -> Library:
    """Return an initialized library below *tmp_path*."""
    library = Library(tmp_path / "library")
    library.initialize()
    return library


def store_payload(entry: LibraryEntry, filename: str, payload: bytes = b"data") -> Path:
    """Write a finished payload into *entry* and return where it landed."""
    staged = entry.reserve(filename)
    staged.write_bytes(payload)
    return entry.commit(staged, filename)


def complete(entry: LibraryEntry, filename: str, payload: bytes = b"data") -> None:
    """Store a payload together with the record that claims it is finished."""
    store_payload(entry, filename, payload)
    record = new_record(make_ref(), entry.key, status=DownloadStatus.COMPLETED)
    entry.write(
        replace(
            record,
            content=ContentRecord(
                filename=filename,
                path=f"{CONTENT_DIRECTORY}/{filename}",
                size=len(payload),
            ),
        )
    )


def test_initializing_creates_the_root_and_its_descriptor(tmp_path: Path) -> None:
    library = make_library(tmp_path)

    descriptor = json.loads(library.descriptor_path.read_text(encoding="utf-8"))
    assert library.root.is_dir()
    assert descriptor["schema"] == LIBRARY_SCHEMA
    assert descriptor["created_at"]


def test_initializing_twice_keeps_the_original_descriptor(tmp_path: Path) -> None:
    library = make_library(tmp_path)
    original = library.descriptor_path.read_text(encoding="utf-8")

    library.initialize()

    assert library.descriptor_path.read_text(encoding="utf-8") == original


def test_the_descriptor_is_not_mistaken_for_a_provider(tmp_path: Path) -> None:
    library = make_library(tmp_path)

    assert DESCRIPTOR_FILENAME not in library.providers()


def test_an_entry_lives_under_its_provider(tmp_path: Path) -> None:
    library = make_library(tmp_path)

    entry = library.entry(make_ref())

    assert entry.path.parent.name == "mega"
    assert entry.path.parent.parent == library.root


def test_addressing_an_entry_creates_nothing(tmp_path: Path) -> None:
    library = make_library(tmp_path)

    entry = library.entry(make_ref())

    assert not entry.path.exists()
    assert entry.exists() is False


def test_two_references_to_the_same_resource_share_an_entry(tmp_path: Path) -> None:
    library = make_library(tmp_path)

    with_key = library.entry(make_ref(secret="0123456789abcdefghijkl"))
    without_key = library.entry(make_ref())

    assert with_key.path == without_key.path


def test_a_record_round_trips_through_the_entry(tmp_path: Path) -> None:
    library = make_library(tmp_path)
    entry = library.entry(make_ref())
    record = new_record(make_ref(), entry.key, status=DownloadStatus.PENDING, name="ubuntu.iso")

    entry.write(record)

    assert entry.exists() is True
    assert entry.read() == record


def test_reading_an_absent_record_reports_nothing(tmp_path: Path) -> None:
    entry = make_library(tmp_path).entry(make_ref())

    assert entry.read() is None


def test_an_unreadable_record_is_reported_rather_than_treated_as_absent(tmp_path: Path) -> None:
    entry = make_library(tmp_path).entry(make_ref())
    entry.path.mkdir(parents=True)
    entry.metadata_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(LibraryRecordError, match="not valid JSON"):
        entry.read()


def test_a_record_that_is_not_an_object_is_reported(tmp_path: Path) -> None:
    entry = make_library(tmp_path).entry(make_ref())
    entry.path.mkdir(parents=True)
    entry.metadata_path.write_text("[]", encoding="utf-8")

    with pytest.raises(LibraryRecordError, match="not a JSON object"):
        entry.read()


def test_writing_a_record_replaces_it_without_leaving_a_temporary(tmp_path: Path) -> None:
    entry = make_library(tmp_path).entry(make_ref())

    entry.write(new_record(make_ref(), entry.key, status=DownloadStatus.PENDING))
    entry.write(new_record(make_ref(), entry.key, status=DownloadStatus.COMPLETED))

    record = entry.read()
    assert record is not None
    assert record.status is DownloadStatus.COMPLETED
    assert list(entry.path.glob("*.tmp")) == []


def test_a_payload_is_staged_before_it_is_committed(tmp_path: Path) -> None:
    entry = make_library(tmp_path).entry(make_ref())

    staged = entry.reserve("ubuntu.iso")

    assert staged.parent == entry.staging_directory
    assert staged.parent.name == STAGING_DIRECTORY


def test_committing_moves_the_payload_into_the_content_directory(tmp_path: Path) -> None:
    entry = make_library(tmp_path).entry(make_ref())

    stored = store_payload(entry, "ubuntu.iso", b"payload")

    assert stored == entry.path / CONTENT_DIRECTORY / "ubuntu.iso"
    assert stored.read_bytes() == b"payload"
    assert not entry.staging_directory.exists() or not list(entry.staging_directory.iterdir())


def test_a_hostile_filename_cannot_escape_the_entry(tmp_path: Path) -> None:
    entry = make_library(tmp_path).entry(make_ref())

    stored = store_payload(entry, "../../escaped.txt")

    assert stored.parent == entry.content_directory
    assert not (tmp_path / "escaped.txt").exists()


def test_a_payload_named_like_the_metadata_cannot_overwrite_it(tmp_path: Path) -> None:
    entry = make_library(tmp_path).entry(make_ref())
    entry.write(new_record(make_ref(), entry.key, status=DownloadStatus.PENDING))

    stored = store_payload(entry, METADATA_FILENAME, b"payload")

    assert stored != entry.metadata_path
    assert entry.read() is not None


def test_discarding_removes_the_staging_directory(tmp_path: Path) -> None:
    entry = make_library(tmp_path).entry(make_ref())
    entry.reserve("ubuntu.iso").write_bytes(b"partial")

    entry.discard()

    assert not entry.staging_directory.exists()


def test_discarding_without_a_staging_directory_is_harmless(tmp_path: Path) -> None:
    make_library(tmp_path).entry(make_ref()).discard()


def test_completeness_needs_both_the_record_and_the_file(tmp_path: Path) -> None:
    library = make_library(tmp_path)
    entry = library.entry(make_ref())

    complete(entry, "ubuntu.iso")

    assert entry.is_complete() is True


def test_a_completed_record_whose_file_is_gone_is_not_complete(tmp_path: Path) -> None:
    entry = make_library(tmp_path).entry(make_ref())
    complete(entry, "ubuntu.iso")

    (entry.content_directory / "ubuntu.iso").unlink()

    assert entry.is_complete() is False


def test_a_pending_record_is_not_complete(tmp_path: Path) -> None:
    entry = make_library(tmp_path).entry(make_ref())
    entry.write(new_record(make_ref(), entry.key, status=DownloadStatus.PENDING))

    assert entry.is_complete() is False


def test_an_unreadable_record_is_not_complete(tmp_path: Path) -> None:
    entry = make_library(tmp_path).entry(make_ref())
    entry.path.mkdir(parents=True)
    entry.metadata_path.write_text("{not json", encoding="utf-8")

    assert entry.is_complete() is False


def test_the_library_lists_its_providers_and_entries(tmp_path: Path) -> None:
    library = make_library(tmp_path)
    for ref in (make_ref("AaBbCcDd"), make_ref("EeFfGgHh", provider="gofile")):
        entry = library.entry(ref)
        entry.write(new_record(ref, entry.key, status=DownloadStatus.COMPLETED))

    assert library.providers() == ("gofile", "mega")
    assert len(list(library.entries())) == 2
    assert len(list(library.entries("mega"))) == 1


def test_listing_an_empty_library_yields_nothing(tmp_path: Path) -> None:
    library = make_library(tmp_path)

    assert library.providers() == ()
    assert list(library.entries()) == []
    assert list(library.entries("mega")) == []


def test_a_library_that_cannot_be_created_reports_a_library_error(tmp_path: Path) -> None:
    blocker = tmp_path / "library"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(LibraryError, match="could not be created"):
        Library(blocker).initialize()
