"""Tests for the metadata document stored beside every resource."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from doubles import make_ref

from maxicrawler.domain import Checksum, DownloadStatus, ResourceKind
from maxicrawler.library import (
    RECORD_SCHEMA,
    ContentRecord,
    LibraryRecordError,
    ResourceRecord,
    new_record,
)
from maxicrawler.library.records import _KNOWN_KEYS

DISCOVERED = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)
DOWNLOADED = datetime(2026, 8, 2, 9, 5, tzinfo=UTC)


def make_record(**overrides: Any) -> ResourceRecord:
    """Return a complete record, overriding individual members."""
    fields: dict[str, Any] = {
        "provider": "mega",
        "key": "aabbccdd-0123456789",
        "resource_id": "AaBbCcDd",
        "kind": ResourceKind.FILE,
        "status": DownloadStatus.COMPLETED,
        "source_url": "https://mega.nz/file/AaBbCcDd",
        "name": "ubuntu.iso",
        "source_document": "docs/links.md",
        "discovered_at": DISCOVERED,
        "downloaded_at": DOWNLOADED,
        "attempts": 1,
        "content": ContentRecord(
            filename="ubuntu.iso",
            path="content/ubuntu.iso",
            size=1024,
            checksums=(Checksum("sha256", "ab" * 32),),
        ),
    }
    fields.update(overrides)
    return ResourceRecord(**fields)


def round_trip(record: ResourceRecord) -> ResourceRecord:
    """Return *record* after a full JSON serialization cycle."""
    return ResourceRecord.from_document(json.loads(json.dumps(record.to_document())))


def test_a_record_survives_a_json_round_trip() -> None:
    record = make_record()

    assert round_trip(record) == record


def test_a_record_without_content_survives_a_round_trip() -> None:
    record = make_record(status=DownloadStatus.FAILED, content=None, error="link is gone")

    assert round_trip(record) == record


def test_a_document_states_its_schema() -> None:
    assert make_record().to_document()["schema"] == RECORD_SCHEMA


def test_a_document_carries_every_field_the_sprint_requires() -> None:
    document = make_record().to_document()

    assert document["provider"] == "mega"
    assert document["source_url"] == "https://mega.nz/file/AaBbCcDd"
    assert document["discovered_at"] == DISCOVERED.isoformat()
    assert document["downloaded_at"] == DOWNLOADED.isoformat()
    assert document["status"] == "completed"
    assert document["content"]["filename"] == "ubuntu.iso"
    assert document["content"]["size"] == 1024
    assert document["content"]["checksums"] == [{"algorithm": "sha256", "value": "ab" * 32}]


def test_an_unknown_member_is_preserved_across_a_round_trip() -> None:
    document = make_record().to_document()
    document["seeded_at"] = "2027-01-01T00:00:00+00:00"

    record = ResourceRecord.from_document(document)

    assert record.extra == {"seeded_at": "2027-01-01T00:00:00+00:00"}
    assert record.to_document()["seeded_at"] == "2027-01-01T00:00:00+00:00"


def test_a_known_member_wins_over_a_stale_copy_in_extra() -> None:
    record = make_record(extra={"status": "pending"})

    assert record.to_document()["status"] == "completed"


def test_a_newer_schema_is_refused_rather_than_misread() -> None:
    document = make_record().to_document()
    document["schema"] = RECORD_SCHEMA + 1

    with pytest.raises(LibraryRecordError, match="newer MaxiCrawler"):
        ResourceRecord.from_document(document)


@pytest.mark.parametrize(
    ("member", "value", "message"),
    [
        ("provider", 5, "'provider' must be a string"),
        ("schema", "one", "'schema' must be an integer"),
        ("kind", "sculpture", "'kind' is not a known ResourceKind"),
        ("status", "half", "'status' is not a known DownloadStatus"),
        ("discovered_at", "yesterday", "'discovered_at' is not an ISO-8601 timestamp"),
        ("attempts", "many", "'attempts' must be an integer"),
        ("content", [], "must be an object"),
    ],
)
def test_an_unreadable_member_is_reported(member: str, value: object, message: str) -> None:
    document = make_record().to_document()
    document[member] = value

    with pytest.raises(LibraryRecordError, match=message):
        ResourceRecord.from_document(document)


def test_a_checksum_list_of_the_wrong_shape_is_reported() -> None:
    document = make_record().to_document()
    document["content"]["checksums"] = "sha256"

    with pytest.raises(LibraryRecordError, match="'checksums' must be an array"):
        ResourceRecord.from_document(document)


def test_a_record_reports_completeness_only_with_content() -> None:
    assert make_record().is_complete is True
    assert make_record(content=None).is_complete is False
    assert make_record(status=DownloadStatus.FAILED).is_complete is False


def test_a_content_record_finds_a_digest_by_algorithm() -> None:
    content = make_record().content

    assert content is not None
    assert content.checksum("sha256") == "ab" * 32
    assert content.checksum("md5") is None


def test_every_declared_member_is_known_to_the_reader() -> None:
    assert set(make_record().to_document()) <= _KNOWN_KEYS


def test_a_new_record_starts_out_pending() -> None:
    ref = make_ref()
    record = new_record(ref, "aabbccdd-0123456789", status=DownloadStatus.PENDING)

    assert record.status is DownloadStatus.PENDING
    assert record.provider == "mega"
    assert record.resource_id == "AaBbCcDd"
    assert record.source_url == ref.url
    assert record.content is None
