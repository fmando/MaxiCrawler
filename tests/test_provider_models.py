"""Tests for the immutable provider domain models."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from maxicrawler.domain import (
    Availability,
    LinkAttribute,
    ProviderCapability,
    ProviderInfo,
    ResourceEntry,
    ResourceInspection,
    ResourceKind,
    ResourceMetadata,
    ResourceRef,
    ResourceSecret,
)

SECRET = "AaBbCcDdEeFfGgHhIiJjKk"


def make_ref(
    resource_id: str = "AaBbCcDd",
    kind: ResourceKind = ResourceKind.FILE,
    *,
    secret: str | None = None,
    parent_id: str | None = None,
) -> ResourceRef:
    """Return a reference without touching a provider."""
    return ResourceRef(
        provider="example",
        resource_id=resource_id,
        kind=kind,
        url=f"https://example.test/{resource_id}",
        secret=None if secret is None else ResourceSecret(secret),
        parent_id=parent_id,
    )


def make_entry(name: str, kind: ResourceKind, size: int | None) -> ResourceEntry:
    """Return one container entry."""
    return ResourceEntry(
        ref=make_ref(name, kind),
        metadata=ResourceMetadata(kind=kind, name=name, size=size),
    )


def test_resource_secret_hides_its_value_from_repr_and_str() -> None:
    secret = ResourceSecret(SECRET)

    assert SECRET not in repr(secret)
    assert SECRET not in str(secret)
    assert repr(secret) == "ResourceSecret(<redacted>)"
    assert str(secret) == "<redacted>"


def test_resource_secret_reveals_its_value_only_on_request() -> None:
    assert ResourceSecret(SECRET).reveal() == SECRET


def test_resource_secret_rejects_an_empty_value() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        ResourceSecret("")


def test_resource_secret_is_immutable() -> None:
    secret = ResourceSecret(SECRET)

    with pytest.raises(AttributeError, match="immutable"):
        secret._value = "other"  # type: ignore[misc]
    with pytest.raises(AttributeError, match="immutable"):
        del secret._value  # type: ignore[misc]
    assert secret.reveal() == SECRET


def test_resource_secret_compares_and_hashes_by_value() -> None:
    assert ResourceSecret(SECRET) == ResourceSecret(SECRET)
    assert ResourceSecret(SECRET) != ResourceSecret("other")
    assert ResourceSecret(SECRET) != SECRET
    assert len({ResourceSecret(SECRET), ResourceSecret(SECRET)}) == 1


def test_resource_ref_keeps_the_secret_out_of_its_repr() -> None:
    ref = make_ref(secret=SECRET)

    assert SECRET not in repr(ref)
    assert ref.has_secret is True
    assert ref.is_contained is False


def test_resource_ref_without_a_secret_reports_it() -> None:
    ref = make_ref()

    assert ref.has_secret is False
    assert ref.secret is None


def test_resource_ref_reports_containment() -> None:
    assert make_ref(parent_id="Folder01").is_contained is True


def test_resource_ref_is_immutable() -> None:
    ref = make_ref()

    with pytest.raises(FrozenInstanceError):
        ref.resource_id = "other"  # type: ignore[misc]


def test_provider_info_reports_advertised_capabilities() -> None:
    info = ProviderInfo(
        name="example",
        version="1.0.0",
        module="tests",
        capabilities=frozenset({ProviderCapability.INSPECT}),
    )

    assert info.supports(ProviderCapability.INSPECT) is True
    assert info.supports(ProviderCapability.DOWNLOAD) is False


def test_provider_info_labels_itself_for_humans() -> None:
    assert ProviderInfo(name="mega", version="1", module="tests").label == "Mega"
    assert (
        ProviderInfo(name="gofile", version="1", module="tests", display_name="GoFile").label
        == "GoFile"
    )


def test_availability_reports_reachability() -> None:
    assert Availability.AVAILABLE.is_available is True
    assert Availability.NOT_FOUND.is_available is False


def test_availability_separates_a_verdict_from_an_open_question() -> None:
    assert Availability.AVAILABLE.is_determined is True
    assert Availability.BLOCKED.is_determined is True
    assert Availability.NOT_FOUND.is_determined is True
    assert Availability.RATE_LIMITED.is_determined is False
    assert Availability.QUOTA_EXCEEDED.is_determined is False
    assert Availability.UNKNOWN.is_determined is False


def test_resource_metadata_exposes_structured_attributes() -> None:
    metadata = ResourceMetadata(
        kind=ResourceKind.FILE,
        name="ubuntu.iso",
        size=5_800_000_000,
        modified_at=datetime(2026, 8, 2, tzinfo=UTC),
        attributes=(LinkAttribute("handle", "AaBbCcDd"),),
    )

    assert metadata.attribute("handle") == "AaBbCcDd"
    assert metadata.attribute("missing") is None


def test_inspection_of_a_file_reports_its_own_size() -> None:
    inspection = ResourceInspection(
        ref=make_ref(),
        availability=Availability.AVAILABLE,
        metadata=ResourceMetadata(kind=ResourceKind.FILE, name="a.iso", size=1024),
    )

    assert inspection.total_size == 1024
    assert inspection.file_count == 0
    assert inspection.kind is ResourceKind.FILE


def test_inspection_of_a_container_counts_and_sums_its_entries() -> None:
    inspection = ResourceInspection(
        ref=make_ref(kind=ResourceKind.FOLDER),
        availability=Availability.AVAILABLE,
        metadata=ResourceMetadata(kind=ResourceKind.FOLDER, name="release"),
        entries=(
            make_entry("a.iso", ResourceKind.FILE, 100),
            make_entry("b.iso", ResourceKind.FILE, 250),
            make_entry("nested", ResourceKind.FOLDER, None),
        ),
    )

    assert inspection.file_count == 2
    assert inspection.folder_count == 1
    assert inspection.total_size == 350


def test_inspection_reports_an_unknown_total_when_a_size_is_missing() -> None:
    inspection = ResourceInspection(
        ref=make_ref(kind=ResourceKind.FOLDER),
        availability=Availability.AVAILABLE,
        entries=(
            make_entry("a.iso", ResourceKind.FILE, 100),
            make_entry("b.iso", ResourceKind.FILE, None),
        ),
    )

    assert inspection.total_size is None


def test_inspection_without_metadata_reports_no_size() -> None:
    inspection = ResourceInspection(ref=make_ref(), availability=Availability.NOT_FOUND)

    assert inspection.total_size is None
    assert inspection.metadata is None
    assert inspection.availability is Availability.NOT_FOUND


def test_inspection_prefers_the_observed_kind_over_the_referenced_one() -> None:
    inspection = ResourceInspection(
        ref=make_ref(kind=ResourceKind.UNKNOWN),
        availability=Availability.AVAILABLE,
        metadata=ResourceMetadata(kind=ResourceKind.FOLDER),
    )

    assert inspection.kind is ResourceKind.FOLDER


def test_inspection_falls_back_to_the_referenced_kind() -> None:
    inspection = ResourceInspection(
        ref=make_ref(kind=ResourceKind.FOLDER),
        availability=Availability.UNKNOWN,
        metadata=ResourceMetadata(kind=ResourceKind.UNKNOWN),
    )

    assert inspection.kind is ResourceKind.FOLDER


def test_inspection_defaults_describe_a_complete_readable_result() -> None:
    inspection = ResourceInspection(ref=make_ref(), availability=Availability.AVAILABLE)

    assert inspection.names_available is True
    assert inspection.truncated is False
    assert inspection.entries == ()
