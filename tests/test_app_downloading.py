"""Tests for the download service both clients go through.

The provider is a stub registered under the Mega name, so every assertion here
is about the service — what it refuses, what it reports, what it stores, what a
client is told while it runs — and none of it about how any host moves bytes.
Nothing opens a socket.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from doubles import StubProvider

from maxicrawler.app import DownloadProgress, DownloadService
from maxicrawler.config import Settings
from maxicrawler.domain import (
    Availability,
    DownloadStatus,
    ProviderCapability,
    ResourceEntry,
    ResourceInspection,
    ResourceKind,
    ResourceMetadata,
    ResourceRef,
)
from maxicrawler.library import FALLBACK_FILENAME, METADATA_FILENAME, Library, new_record
from maxicrawler.providers import ProviderRegistry

KEY = "0123456789abcdefghijkl"
FILE_URL = f"https://mega.nz/file/AaBbCcDd#{KEY}"
OTHER_URL = f"https://mega.nz/file/EeFfGgHh#{KEY}"
FOLDER_URL = f"https://mega.nz/folder/FolderAA#{KEY}"
UNSUPPORTED_URL = "https://example.org/downloads/report.zip"
PAYLOAD = b"stub payload"
DOWNLOADS = frozenset({ProviderCapability.INSPECT, ProviderCapability.DOWNLOAD})


def make_provider(**overrides: object) -> StubProvider:
    """Return a stub provider that answers for Mega links and can transfer."""
    settings: dict[str, object] = {
        "url_prefix": "https://mega.nz/",
        "capabilities": DOWNLOADS,
        "payload": PAYLOAD,
    }
    settings.update(overrides)
    return StubProvider("mega", **settings)  # type: ignore[arg-type]


def make_service(
    tmp_path: Path, provider: StubProvider | None = None
) -> tuple[DownloadService, Library]:
    """Return a service storing into a library below *tmp_path*."""
    library = Library(tmp_path / "library")
    registry = ProviderRegistry([provider if provider is not None else make_provider()])
    settings = Settings(library_path=library.root)
    return DownloadService(settings, providers=registry, library=library), library


def folder_inspection(*, truncated: bool = False) -> ResourceInspection:
    """Return an inspection describing a folder holding two files."""
    ref = ResourceRef(
        provider="mega",
        resource_id="FolderAA",
        kind=ResourceKind.FOLDER,
        url="https://mega.nz/folder/FolderAA",
    )
    return ResourceInspection(
        ref=ref,
        availability=Availability.AVAILABLE,
        metadata=ResourceMetadata(kind=ResourceKind.FOLDER, name="Releases"),
        entries=tuple(
            ResourceEntry(
                ref=ResourceRef(
                    provider="mega",
                    resource_id=handle,
                    kind=ResourceKind.FILE,
                    url=ref.url,
                    parent_id="FolderAA",
                ),
                metadata=ResourceMetadata(kind=ResourceKind.FILE, name=name, size=len(PAYLOAD)),
            )
            for handle, name in (("FileAAA1", "ubuntu.iso"), ("FileAAA2", "checksums.txt"))
        ),
        truncated=truncated,
    )


def gone_inspection() -> ResourceInspection:
    """Return the verdict for a share that is no longer there."""
    return ResourceInspection(
        ref=ResourceRef(
            provider="mega", resource_id="AaBbCcDd", kind=ResourceKind.FILE, url=FILE_URL
        ),
        availability=Availability.NOT_FOUND,
    )


# --- the happy path -----------------------------------------------------------


def test_a_single_link_lands_in_the_library(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)

    summary = service.download(FILE_URL)

    assert summary.status is DownloadStatus.COMPLETED
    assert summary.succeeded is True
    assert summary.path is not None
    assert summary.path.read_bytes() == PAYLOAD
    assert summary.bytes_written == len(PAYLOAD)
    assert summary.files_total == 1
    assert summary.files_completed == 1
    assert summary.library_root == library.root


def test_the_summary_names_the_file_rather_than_its_handle(tmp_path: Path) -> None:
    """What `inspect_files` buys: a name and a denominator before the transfer."""
    service, _ = make_service(tmp_path)

    summary = service.download(FILE_URL)

    assert summary.label == "stub.bin"
    assert summary.total_bytes == 1024


def test_the_key_never_reaches_the_summary(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)

    summary = service.download(FILE_URL)

    assert summary.url == "https://mega.nz/file/AaBbCcDd"
    assert KEY not in repr(summary)


def test_a_second_download_of_the_same_link_is_skipped(tmp_path: Path) -> None:
    provider = make_provider()
    service, _ = make_service(tmp_path, provider)
    service.download(FILE_URL)

    summary = service.download(FILE_URL)

    assert summary.status is DownloadStatus.SKIPPED
    assert summary.succeeded is True
    assert summary.reason == "the library already holds it"
    assert len(provider.downloaded) == 1


def test_a_folder_link_is_one_request_holding_several_files(tmp_path: Path) -> None:
    """Not a batch of links: one link that turned out to hold more than one file."""
    provider = make_provider(kind=ResourceKind.FOLDER, inspection=folder_inspection())
    service, _ = make_service(tmp_path, provider)

    summary = service.download(FOLDER_URL)

    assert summary.status is DownloadStatus.COMPLETED
    assert summary.files_total == 2
    assert summary.files_completed == 2
    assert summary.path is None


# --- what a client is told while it runs --------------------------------------


def test_progress_is_reported_as_the_transfer_moves(tmp_path: Path) -> None:
    reported: list[DownloadProgress] = []
    service, _ = make_service(tmp_path)

    summary = service.download(FILE_URL, on_progress=reported.append)

    assert reported
    assert [progress.bytes_written for progress in reported] == sorted(
        progress.bytes_written for progress in reported
    )
    assert reported[-1].status is DownloadStatus.COMPLETED
    assert reported[-1].bytes_written == summary.bytes_written
    assert reported[-1].files_finished == 1


def test_progress_names_the_file_and_its_total_from_the_first_frame(tmp_path: Path) -> None:
    reported: list[DownloadProgress] = []
    service, _ = make_service(tmp_path)

    service.download(FILE_URL, on_progress=reported.append)

    assert reported[0].label == "stub.bin"
    assert reported[0].total_bytes == 1024
    assert reported[0].fraction == 0.0


def test_progress_has_no_fraction_when_nothing_stated_a_total() -> None:
    """A bar at zero for two minutes would claim progress nobody can see."""
    unknown = DownloadProgress(label="x", status=DownloadStatus.RUNNING, bytes_written=17)

    assert unknown.fraction is None
    assert DownloadProgress(
        label="x", status=DownloadStatus.RUNNING, bytes_written=5, total_bytes=10
    ).fraction == pytest.approx(0.5)


def test_a_listener_is_optional(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)

    assert service.download(FILE_URL).succeeded is True


# --- what is refused ----------------------------------------------------------


def test_a_filesystem_path_is_refused_even_though_it_holds_links(tmp_path: Path) -> None:
    """The one rule that keeps a click from making the server read its own disk."""
    document = tmp_path / "links.md"
    document.write_text(f"[a]({FILE_URL})\n", encoding="utf-8")
    provider = make_provider()
    service, _ = make_service(tmp_path, provider)

    with pytest.raises(ValueError, match="not an absolute HTTP"):
        service.download(str(document))

    assert provider.downloaded == []


def test_a_directory_is_refused(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)

    with pytest.raises(ValueError, match="not an absolute HTTP"):
        service.download(str(tmp_path))


@pytest.mark.parametrize("value", ["", "   ", "ftp://example.org/x", "javascript:alert(1)", "x"])
def test_only_an_http_url_is_accepted(tmp_path: Path, value: str) -> None:
    service, _ = make_service(tmp_path)

    with pytest.raises(ValueError, match="not an absolute HTTP"):
        service.download(value)


def test_a_link_no_provider_handles_is_a_finding_rather_than_a_failure(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)

    summary = service.download(UNSUPPORTED_URL)

    assert summary.status is DownloadStatus.FAILED
    assert summary.succeeded is False
    assert "no provider" in (summary.reason or "")
    assert summary.files_total == 0


def test_a_dead_share_is_reported_before_anything_is_transferred(tmp_path: Path) -> None:
    provider = make_provider(inspection=gone_inspection())
    service, _ = make_service(tmp_path, provider)

    summary = service.download(FILE_URL)

    assert summary.status is DownloadStatus.FAILED
    assert "not found" in (summary.reason or "")
    assert provider.downloaded == []


# --- which links get an action at all -----------------------------------------


def test_downloadable_answers_from_the_link_alone(tmp_path: Path) -> None:
    provider = make_provider()
    service, _ = make_service(tmp_path, provider)

    answer = service.downloadable([FILE_URL, UNSUPPORTED_URL, "not a url"])

    assert answer == frozenset({FILE_URL})
    assert provider.inspected == []
    assert provider.downloaded == []


def test_a_provider_that_cannot_transfer_offers_no_download(tmp_path: Path) -> None:
    """Capability, not name: an inspect-only registry can fetch nothing."""
    provider = make_provider(capabilities=frozenset({ProviderCapability.INSPECT}))
    service, _ = make_service(tmp_path, provider)

    assert service.can_download(FILE_URL) is False
    assert service.can_download(UNSUPPORTED_URL) is False


# --- the library --------------------------------------------------------------


def test_stored_downloads_lists_what_a_download_wrote(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    summary = service.download(FILE_URL)

    items = service.stored_downloads()

    assert len(items) == 1
    item = items[0]
    assert item.provider == "mega"
    assert item.name == "stub.bin"
    assert item.size == len(PAYLOAD)
    assert item.path == summary.path
    assert item.source_url == "https://mega.nz/file/AaBbCcDd"
    assert item.downloaded_at is not None


def test_a_library_that_was_never_written_lists_nothing(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)

    assert service.stored_downloads() == ()


def test_the_newest_download_is_listed_first(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write_entry(library, "AaBbCcDd", "old.bin", datetime(2026, 1, 1, tzinfo=UTC))
    write_entry(library, "EeFfGgHh", "new.bin", datetime(2026, 8, 9, tzinfo=UTC))

    names = [item.name for item in service.stored_downloads()]

    assert names == ["new.bin", "old.bin"]


def test_a_listing_can_be_capped(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write_entry(library, "AaBbCcDd", "old.bin", datetime(2026, 1, 1, tzinfo=UTC))
    write_entry(library, "EeFfGgHh", "new.bin", datetime(2026, 8, 9, tzinfo=UTC))

    assert [item.name for item in service.stored_downloads(limit=1)] == ["new.bin"]


def test_one_damaged_entry_does_not_empty_the_listing(tmp_path: Path) -> None:
    """A library is repairable; a page that refuses to render is not."""
    service, library = make_service(tmp_path)
    service.download(FILE_URL)
    broken = library.root / "mega" / "broken"
    broken.mkdir(parents=True)
    (broken / METADATA_FILENAME).write_text("{not json", encoding="utf-8")

    assert [item.name for item in service.stored_downloads()] == ["stub.bin"]


def test_an_entry_without_a_payload_is_not_an_item(tmp_path: Path) -> None:
    """A recorded failure belongs on the download that produced it, not here."""
    provider = make_provider(failure=RuntimeError("boom"))
    service, _ = make_service(tmp_path, provider)

    with pytest.raises(RuntimeError):
        service.download(FILE_URL)

    assert service.stored_downloads() == ()


def test_an_unnamed_resource_falls_back_to_what_it_was_stored_as(tmp_path: Path) -> None:
    """A share published without its key has no readable name at all."""
    nameless = ResourceInspection(
        ref=ResourceRef(
            provider="mega", resource_id="AaBbCcDd", kind=ResourceKind.FILE, url=FILE_URL
        ),
        availability=Availability.AVAILABLE,
        metadata=ResourceMetadata(kind=ResourceKind.FILE, size=len(PAYLOAD)),
        names_available=False,
    )
    service, _ = make_service(tmp_path, make_provider(inspection=nameless, content_name=None))

    summary = service.download(FILE_URL)

    assert summary.label == "AaBbCcDd"
    assert [item.name for item in service.stored_downloads()] == [FALLBACK_FILENAME]


def write_entry(library: Library, handle: str, filename: str, moment: datetime) -> None:
    """Write a finished library entry by hand, to control when it happened."""
    ref = ResourceRef(
        provider="mega",
        resource_id=handle,
        kind=ResourceKind.FILE,
        url=f"https://mega.nz/file/{handle}",
    )
    entry = library.entry(ref)
    payload = entry.content_path(filename)
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(PAYLOAD)
    record = new_record(ref, entry.key, status=DownloadStatus.COMPLETED, name=filename)
    document = record.to_document()
    document["downloaded_at"] = moment.isoformat()
    document["content"] = {
        "filename": filename,
        "path": f"content/{filename}",
        "size": len(PAYLOAD),
        "checksums": [],
    }
    entry.path.mkdir(parents=True, exist_ok=True)
    entry.metadata_path.write_text(json.dumps(document), encoding="utf-8")
