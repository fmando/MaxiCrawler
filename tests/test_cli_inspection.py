"""Tests for the pure rendering of resource inspections."""

import json
from datetime import UTC, datetime

import pytest

from maxicrawler.cli.inspection import (
    EXIT_AVAILABLE,
    EXIT_UNAVAILABLE,
    EXIT_UNDETERMINED,
    exit_code_for,
    format_size,
    inspection_document,
    render_inspection,
    render_json,
)
from maxicrawler.domain import (
    Availability,
    ProviderInfo,
    ResourceEntry,
    ResourceInspection,
    ResourceKind,
    ResourceMetadata,
    ResourceRef,
    ResourceSecret,
)

PROVIDER = ProviderInfo(
    name="mega", version="0.1.0", module="tests", display_name="Mega", description="stub"
)


def ref(
    kind: ResourceKind = ResourceKind.FILE, *, resource_id: str = "AaBbCcDd", key: bool = True
) -> ResourceRef:
    """Return a reference for rendering tests."""
    return ResourceRef(
        provider="mega",
        resource_id=resource_id,
        kind=kind,
        url=f"https://mega.nz/{kind.value}/{resource_id}",
        secret=ResourceSecret("KeyMaterial") if key else None,
    )


def entry(name: str | None, kind: ResourceKind, size: int | None) -> ResourceEntry:
    """Return one container entry."""
    return ResourceEntry(
        ref=ref(kind, resource_id=name or "Handle01"),
        metadata=ResourceMetadata(kind=kind, name=name, size=size),
    )


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (None, "unknown"),
        (0, "0 B"),
        (999, "999 B"),
        (1000, "1.0 KB"),
        (1_048_576, "1.0 MB"),
        (5_800_000_000, "5.8 GB"),
        (2_500_000_000_000, "2.5 TB"),
        (3_000_000_000_000_000, "3.0 PB"),
        (9_000_000_000_000_000_000, "9000.0 PB"),
    ],
)
def test_size_is_rendered_in_decimal_units(size: int | None, text: str) -> None:
    assert format_size(size) == text


@pytest.mark.parametrize(
    ("availability", "code"),
    [
        (Availability.AVAILABLE, EXIT_AVAILABLE),
        (Availability.NOT_FOUND, EXIT_UNAVAILABLE),
        (Availability.ACCESS_DENIED, EXIT_UNAVAILABLE),
        (Availability.BLOCKED, EXIT_UNAVAILABLE),
        (Availability.RATE_LIMITED, EXIT_UNDETERMINED),
        (Availability.QUOTA_EXCEEDED, EXIT_UNDETERMINED),
        (Availability.UNKNOWN, EXIT_UNDETERMINED),
    ],
)
def test_the_exit_code_separates_a_verdict_from_an_open_question(
    availability: Availability, code: int
) -> None:
    assert exit_code_for(availability) == code


def test_a_file_renders_the_documented_layout() -> None:
    inspection = ResourceInspection(
        ref=ref(),
        availability=Availability.AVAILABLE,
        metadata=ResourceMetadata(kind=ResourceKind.FILE, name="ubuntu.iso", size=5_800_000_000),
    )

    assert render_inspection(inspection, PROVIDER) == (
        "Provider: Mega\nType: File\nName: ubuntu.iso\nSize: 5.8 GB\nAvailable: Yes"
    )


def test_an_unreachable_resource_renders_without_metadata_lines() -> None:
    inspection = ResourceInspection(ref=ref(), availability=Availability.NOT_FOUND)

    assert render_inspection(inspection, PROVIDER) == (
        "Provider: Mega\nType: File\nAvailable: No (not found)"
    )


def test_an_unreadable_name_is_named_and_explained() -> None:
    inspection = ResourceInspection(
        ref=ref(key=False),
        availability=Availability.AVAILABLE,
        metadata=ResourceMetadata(kind=ResourceKind.FILE, size=1000),
        names_available=False,
    )

    report = render_inspection(inspection, PROVIDER)

    assert "Name: unavailable (encrypted)" in report
    assert report.endswith("Names stay encrypted: the link carries no usable decryption key.")


def test_a_folder_renders_its_contents() -> None:
    inspection = ResourceInspection(
        ref=ref(ResourceKind.FOLDER),
        availability=Availability.AVAILABLE,
        metadata=ResourceMetadata(kind=ResourceKind.FOLDER, name="release"),
        entries=(
            entry("archive", ResourceKind.FOLDER, None),
            entry("ubuntu.iso", ResourceKind.FILE, 5_800_000_000),
        ),
    )

    report = render_inspection(inspection, PROVIDER)

    assert "Files: 1" in report
    assert "Folders: 1" in report
    assert "Contents:" in report
    assert "\n  archive/\n" in report
    assert "  ubuntu.iso  5.8 GB" in report


def test_an_empty_folder_renders_counts_without_a_listing() -> None:
    inspection = ResourceInspection(
        ref=ref(ResourceKind.FOLDER),
        availability=Availability.AVAILABLE,
        metadata=ResourceMetadata(kind=ResourceKind.FOLDER, name="empty"),
    )

    report = render_inspection(inspection, PROVIDER)

    assert "Files: 0" in report
    assert "Contents:" not in report


def test_a_truncated_folder_says_so() -> None:
    inspection = ResourceInspection(
        ref=ref(ResourceKind.FOLDER),
        availability=Availability.AVAILABLE,
        metadata=ResourceMetadata(kind=ResourceKind.FOLDER, name="release"),
        entries=(entry("a.iso", ResourceKind.FILE, 1),),
        truncated=True,
    )

    assert "more entries were not listed" in render_inspection(inspection, PROVIDER)


def test_a_nameless_entry_falls_back_to_its_handle() -> None:
    inspection = ResourceInspection(
        ref=ref(ResourceKind.FOLDER),
        availability=Availability.AVAILABLE,
        metadata=ResourceMetadata(kind=ResourceKind.FOLDER),
        entries=(entry(None, ResourceKind.FILE, 10),),
        names_available=False,
    )

    assert "Handle01" in render_inspection(inspection, PROVIDER)


def test_the_json_document_describes_the_resource() -> None:
    inspection = ResourceInspection(
        ref=ref(),
        availability=Availability.AVAILABLE,
        metadata=ResourceMetadata(
            kind=ResourceKind.FILE,
            name="ubuntu.iso",
            size=1000,
            modified_at=datetime(2026, 8, 2, tzinfo=UTC),
        ),
    )

    document = json.loads(render_json(inspection, PROVIDER))

    assert document["provider"] == "mega"
    assert document["type"] == "file"
    assert document["availability"] == "available"
    assert document["available"] is True
    assert document["has_key"] is True
    assert document["modified_at"].startswith("2026-08-02")


def test_the_json_document_never_carries_the_secret() -> None:
    inspection = ResourceInspection(
        ref=ref(),
        availability=Availability.AVAILABLE,
        metadata=ResourceMetadata(kind=ResourceKind.FILE, name="a.iso", size=1),
    )

    rendered = render_json(inspection, PROVIDER)

    assert "KeyMaterial" not in rendered
    assert "secret" not in rendered


def test_the_json_document_of_a_file_omits_container_fields() -> None:
    inspection = ResourceInspection(
        ref=ref(),
        availability=Availability.AVAILABLE,
        metadata=ResourceMetadata(kind=ResourceKind.FILE, name="a.iso", size=1),
    )

    document = inspection_document(inspection, PROVIDER)

    assert "entries" not in document
    assert "file_count" not in document


def test_the_json_document_of_an_unreachable_resource_reports_nulls() -> None:
    document = inspection_document(
        ResourceInspection(ref=ref(), availability=Availability.BLOCKED), PROVIDER
    )

    assert document["available"] is False
    assert document["availability"] == "blocked"
    assert document["name"] is None
    assert document["size"] is None


def test_the_json_document_omits_findings_it_did_not_make() -> None:
    document = inspection_document(
        ResourceInspection(ref=ref(), availability=Availability.NOT_FOUND), PROVIDER
    )

    assert "names_available" not in document
    assert "modified_at" not in document


def test_the_json_document_reports_readability_when_metadata_exists() -> None:
    document = inspection_document(
        ResourceInspection(
            ref=ref(key=False),
            availability=Availability.AVAILABLE,
            metadata=ResourceMetadata(kind=ResourceKind.FILE, size=1),
            names_available=False,
        ),
        PROVIDER,
    )

    assert document["names_available"] is False
