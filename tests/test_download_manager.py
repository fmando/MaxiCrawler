"""Tests for the provider-independent download manager.

The provider here is a stub registered under the Mega name, so every assertion
is about orchestration — planning, queueing, skipping, recording, reporting —
and none of it about how any particular host moves bytes. That is the property
the layer exists for.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from doubles import RecordingProgressReporter, StubProvider

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
from maxicrawler.downloader import (
    DownloadManager,
    DownloadPlanner,
    SourceError,
    SourceItem,
    SourceResolver,
)
from maxicrawler.library import METADATA_FILENAME, Library
from maxicrawler.providers import ProviderRegistry, ProviderTransportError

FILE_URL = "https://mega.nz/file/AaBbCcDd#0123456789abcdefghijkl"
FOLDER_URL = "https://mega.nz/folder/FolderAA#0123456789abcdefghijkl"
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


def make_manager(
    tmp_path: Path,
    provider: StubProvider | None = None,
    *,
    reporter: RecordingProgressReporter | None = None,
) -> tuple[DownloadManager, Library]:
    """Return a manager storing into a library below *tmp_path*."""
    registry = ProviderRegistry([provider if provider is not None else make_provider()])
    library = Library(tmp_path / "library")
    manager = DownloadManager(
        registry,
        library,
        reporter=reporter,
        clock=lambda: datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
    )
    return manager, library


def folder_inspection(ref: ResourceRef, *, truncated: bool = False) -> ResourceInspection:
    """Return an inspection describing a folder holding two files and a folder."""
    return ResourceInspection(
        ref=ref,
        availability=Availability.AVAILABLE,
        metadata=ResourceMetadata(kind=ResourceKind.FOLDER, name="Ubuntu Releases"),
        entries=(
            ResourceEntry(
                ref=ResourceRef(
                    provider="mega",
                    resource_id="FileAAA1",
                    kind=ResourceKind.FILE,
                    url=ref.url,
                    parent_id="FolderAA",
                ),
                metadata=ResourceMetadata(kind=ResourceKind.FILE, name="ubuntu.iso", size=12),
            ),
            ResourceEntry(
                ref=ResourceRef(
                    provider="mega",
                    resource_id="SubFldr1",
                    kind=ResourceKind.FOLDER,
                    url=ref.url,
                    parent_id="FolderAA",
                ),
                metadata=ResourceMetadata(kind=ResourceKind.FOLDER, name="archive"),
            ),
            ResourceEntry(
                ref=ResourceRef(
                    provider="mega",
                    resource_id="FileAAA2",
                    kind=ResourceKind.FILE,
                    url=ref.url,
                    parent_id="SubFldr1",
                ),
                metadata=ResourceMetadata(kind=ResourceKind.FILE, name="checksums.txt", size=12),
            ),
        ),
        truncated=truncated,
    )


def stored_record(library: Library, entry_path: Path) -> dict[str, object]:
    """Return the metadata document stored at *entry_path*."""
    document = json.loads((entry_path / METADATA_FILENAME).read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def test_a_single_link_is_downloaded_into_the_library(tmp_path: Path) -> None:
    manager, library = make_manager(tmp_path)

    report = manager.download(FILE_URL)

    assert report.succeeded is True
    assert len(report.completed) == 1
    outcome = report.completed[0]
    assert outcome.path is not None
    assert outcome.path.read_bytes() == PAYLOAD
    assert outcome.path.parent == library.entry(outcome.job.ref).content_directory
    assert outcome.bytes_written == len(PAYLOAD)


def test_the_stored_metadata_describes_the_download(tmp_path: Path) -> None:
    manager, library = make_manager(tmp_path)

    report = manager.download(FILE_URL)

    entry = library.entry(report.completed[0].job.ref)
    document = stored_record(library, entry.path)
    assert document["provider"] == "mega"
    assert document["resource_id"] == "AaBbCcDd"
    assert document["status"] == DownloadStatus.COMPLETED.value
    assert document["source_url"] == "https://mega.nz/file/AaBbCcDd"
    assert document["discovered_at"] == "2026-08-02T12:00:00+00:00"
    assert document["downloaded_at"] == "2026-08-02T12:00:00+00:00"
    assert document["attempts"] == 1
    assert document["content"]["filename"] == "stub.bin"
    assert document["content"]["size"] == len(PAYLOAD)
    assert document["content"]["checksums"][0]["algorithm"] == "sha256"


def test_the_decryption_key_never_reaches_the_stored_metadata(tmp_path: Path) -> None:
    manager, library = make_manager(tmp_path)

    report = manager.download(FILE_URL)

    entry = library.entry(report.completed[0].job.ref)
    assert "0123456789abcdefghijkl" not in entry.metadata_path.read_text(encoding="utf-8")


def test_a_folder_becomes_one_job_per_file(tmp_path: Path) -> None:
    provider = make_provider(kind=ResourceKind.FOLDER)
    provider._inspection = folder_inspection(  # noqa: SLF001 - the stub has no other seam
        ResourceRef(
            provider="mega",
            resource_id="FolderAA",
            kind=ResourceKind.FOLDER,
            url="https://mega.nz/folder/FolderAA",
        )
    )
    manager, _ = make_manager(tmp_path, provider)

    report = manager.download(FOLDER_URL)

    assert [outcome.label for outcome in report.completed] == ["ubuntu.iso", "checksums.txt"]
    assert report.succeeded is True


def test_a_truncated_listing_is_reported_rather_than_hidden(tmp_path: Path) -> None:
    provider = make_provider(kind=ResourceKind.FOLDER)
    provider._inspection = folder_inspection(  # noqa: SLF001 - the stub has no other seam
        ResourceRef(
            provider="mega",
            resource_id="FolderAA",
            kind=ResourceKind.FOLDER,
            url="https://mega.nz/folder/FolderAA",
        ),
        truncated=True,
    )
    manager, _ = make_manager(tmp_path, provider)

    report = manager.download(FOLDER_URL)

    assert len(report.completed) == 2
    assert report.succeeded is False
    assert "more entries" in report.unresolved[0].reason


def test_an_existing_download_is_skipped_without_contacting_the_provider(
    tmp_path: Path,
) -> None:
    provider = make_provider()
    manager, _ = make_manager(tmp_path, provider)
    manager.download(FILE_URL)

    report = manager.download(FILE_URL)

    assert len(report.skipped) == 1
    assert report.skipped[0].reason == "the library already holds it"
    assert len(provider.downloaded) == 1
    assert report.succeeded is True


def test_a_skipped_download_still_points_at_the_stored_payload(tmp_path: Path) -> None:
    manager, _ = make_manager(tmp_path)
    manager.download(FILE_URL)

    report = manager.download(FILE_URL)

    path = report.skipped[0].path
    assert path is not None
    assert path.read_bytes() == PAYLOAD


def test_a_download_whose_payload_was_deleted_is_fetched_again(tmp_path: Path) -> None:
    provider = make_provider()
    manager, library = make_manager(tmp_path, provider)
    first = manager.download(FILE_URL)
    assert first.completed[0].path is not None
    first.completed[0].path.unlink()

    report = manager.download(FILE_URL)

    assert len(report.completed) == 1
    assert len(provider.downloaded) == 2
    entry = library.entry(report.completed[0].job.ref)
    assert stored_record(library, entry.path)["attempts"] == 2


def test_a_failing_provider_produces_a_failed_outcome(tmp_path: Path) -> None:
    provider = make_provider(failure=ProviderTransportError("connection reset"))
    manager, _ = make_manager(tmp_path, provider)

    report = manager.download(FILE_URL)

    assert len(report.failed) == 1
    assert report.failed[0].reason == "connection reset"
    assert report.succeeded is False


def test_a_failure_is_recorded_so_a_later_run_retries(tmp_path: Path) -> None:
    provider = make_provider(failure=ProviderTransportError("connection reset"))
    manager, library = make_manager(tmp_path, provider)

    report = manager.download(FILE_URL)

    entry = library.entry(report.failed[0].job.ref)
    document = stored_record(library, entry.path)
    assert document["status"] == DownloadStatus.FAILED.value
    assert document["error"] == "connection reset"
    assert document["content"] is None
    assert entry.is_complete() is False


def test_a_failed_transfer_leaves_no_payload_behind(tmp_path: Path) -> None:
    provider = make_provider(failure=ProviderTransportError("connection reset"))
    manager, library = make_manager(tmp_path, provider)

    report = manager.download(FILE_URL)

    entry = library.entry(report.failed[0].job.ref)
    assert not entry.content_directory.exists()
    assert not entry.staging_directory.exists()


def test_one_bad_link_does_not_stop_the_others(tmp_path: Path, monkeypatch: object) -> None:
    document = tmp_path / "links.md"
    document.write_text(
        f"- {FILE_URL}\n- https://example.test/not-a-share\n",
        encoding="utf-8",
    )
    manager, _ = make_manager(tmp_path)

    report = manager.download(str(document))

    assert len(report.completed) == 1
    assert len(report.unresolved) == 1
    assert report.unresolved[0].reason == "no provider can handle this link"


def test_an_unreadable_stored_record_fails_rather_than_overwriting(tmp_path: Path) -> None:
    manager, library = make_manager(tmp_path)
    plan = manager.plan(FILE_URL)
    entry = library.entry(plan.jobs[0].ref)
    entry.path.mkdir(parents=True)
    entry.metadata_path.write_text("{not json", encoding="utf-8")

    report = manager.run(plan)

    assert len(report.failed) == 1
    assert "not valid JSON" in (report.failed[0].reason or "")
    assert entry.metadata_path.read_text(encoding="utf-8") == "{not json"


def test_planning_transfers_nothing(tmp_path: Path) -> None:
    provider = make_provider()
    manager, library = make_manager(tmp_path, provider)

    plan = manager.plan(FILE_URL)

    assert len(plan.jobs) == 1
    assert plan.total_size is None
    assert provider.downloaded == []
    assert not library.root.exists()


def test_a_file_link_is_planned_without_asking_the_provider(tmp_path: Path) -> None:
    """The default, and what keeps a run over two hundred links to one request each."""
    provider = make_provider()
    manager, _ = make_manager(tmp_path, provider)

    plan = manager.plan(FILE_URL)

    assert provider.inspected == []
    assert plan.jobs[0].name is None
    assert plan.jobs[0].size is None
    assert plan.jobs[0].label == "AaBbCcDd"


def test_a_file_link_can_be_described_before_it_is_transferred(tmp_path: Path) -> None:
    """What a single deliberate download asks for: a name and a denominator."""
    provider = make_provider()
    manager, _ = make_manager(tmp_path, provider)

    plan = manager.plan(FILE_URL, inspect_files=True)

    assert provider.inspected != []
    assert plan.jobs[0].name == "stub.bin"
    assert plan.jobs[0].size == 1024
    assert plan.total_size == 1024


def test_an_inspected_file_link_that_is_gone_is_reported_before_any_transfer(
    tmp_path: Path,
) -> None:
    """The other half of asking: a verdict arrives instead of a doomed transfer."""
    provider = make_provider(
        inspection=ResourceInspection(
            ref=ResourceRef(
                provider="mega", resource_id="AaBbCcDd", kind=ResourceKind.FILE, url=FILE_URL
            ),
            availability=Availability.NOT_FOUND,
        )
    )
    manager, _ = make_manager(tmp_path, provider)

    plan = manager.plan(FILE_URL, inspect_files=True)

    assert plan.jobs == ()
    assert len(plan.unresolved) == 1
    assert "not found" in plan.unresolved[0].reason
    assert provider.downloaded == []


def test_describing_a_file_link_first_changes_nothing_about_the_transfer(
    tmp_path: Path,
) -> None:
    """Same payload, same entry: the option buys information, not behaviour."""
    manager, library = make_manager(tmp_path)

    report = manager.download(FILE_URL, inspect_files=True)

    assert len(report.completed) == 1
    outcome = report.completed[0]
    assert outcome.path is not None
    assert outcome.path.read_bytes() == PAYLOAD
    assert library.descriptor_path.is_file()


def test_the_library_is_created_by_a_run(tmp_path: Path) -> None:
    manager, library = make_manager(tmp_path)

    manager.download(FILE_URL)

    assert library.descriptor_path.is_file()


def test_the_same_link_listed_twice_is_downloaded_once(tmp_path: Path) -> None:
    provider = make_provider()
    manager, _ = make_manager(tmp_path, provider)
    plan = manager.plan(FILE_URL)
    doubled = type(plan)(jobs=plan.jobs + plan.jobs, unresolved=plan.unresolved)

    report = manager.run(doubled)

    assert len(report.outcomes) == 1
    assert len(provider.downloaded) == 1


def test_progress_is_reported_around_every_transfer(tmp_path: Path) -> None:
    reporter = RecordingProgressReporter()
    manager, _ = make_manager(tmp_path, reporter=reporter)

    manager.download(FILE_URL)

    assert reporter.events == ["begin", "started", "finished", "end"]
    assert reporter.started_jobs == [("AaBbCcDd", None)]
    assert reporter.advanced_totals[-1] == len(PAYLOAD)
    assert reporter.finished_jobs[0].status is DownloadStatus.COMPLETED


def test_a_skipped_transfer_is_reported_without_a_progress_bar(tmp_path: Path) -> None:
    reporter = RecordingProgressReporter()
    manager, _ = make_manager(tmp_path)
    manager.download(FILE_URL)
    manager, _ = make_manager(tmp_path, reporter=reporter)

    manager.download(FILE_URL)

    assert reporter.events == ["begin", "finished", "end"]
    assert reporter.finished_jobs[0].status is DownloadStatus.SKIPPED


def test_the_run_is_bracketed_even_when_a_worker_explodes(tmp_path: Path) -> None:
    reporter = RecordingProgressReporter()
    manager, _ = make_manager(tmp_path, reporter=reporter)
    plan = manager.plan(FILE_URL)

    class Exploding:
        def execute(self, job: object) -> object:
            msg = "the worker gave up"
            raise RuntimeError(msg)

    manager._worker = Exploding()  # type: ignore[assignment]  # noqa: SLF001

    with pytest.raises(RuntimeError, match="the worker gave up"):
        manager.run(plan)

    assert reporter.events == ["begin", "end"]


def test_an_unavailable_resource_is_reported_with_the_providers_verdict(
    tmp_path: Path,
) -> None:
    provider = make_provider(kind=ResourceKind.FOLDER)
    provider._inspection = ResourceInspection(  # noqa: SLF001 - the stub has no other seam
        ref=ResourceRef(
            provider="mega",
            resource_id="FolderAA",
            kind=ResourceKind.FOLDER,
            url="https://mega.nz/folder/FolderAA",
        ),
        availability=Availability.NOT_FOUND,
    )
    manager, _ = make_manager(tmp_path, provider)

    report = manager.download(FOLDER_URL)

    assert report.outcomes == ()
    assert report.unresolved[0].reason == "the provider reports it as not found"


def test_a_provider_that_cannot_download_is_reported(tmp_path: Path) -> None:
    provider = make_provider(capabilities=frozenset({ProviderCapability.INSPECT}))
    manager, _ = make_manager(tmp_path, provider)

    report = manager.download(FILE_URL)

    assert report.unresolved[0].reason == "the Mega provider cannot transfer content"


def test_a_planner_reports_a_url_no_plugin_understands() -> None:
    planner = DownloadPlanner(ProviderRegistry([make_provider()]))

    plan = planner.plan([SourceItem(url="not a url at all")])

    assert plan.jobs == ()
    assert plan.unresolved[0].reason == "not an absolute HTTP(S) URL"


def test_a_bad_source_is_reported_before_anything_runs(tmp_path: Path) -> None:
    manager, _ = make_manager(tmp_path)

    with pytest.raises(SourceError, match="neither an HTTP\\(S\\) URL"):
        manager.download(str(tmp_path / "nowhere.txt"))


def test_the_source_document_is_recorded_with_the_download(tmp_path: Path) -> None:
    document = tmp_path / "links.md"
    document.write_text(f"- {FILE_URL}\n", encoding="utf-8")
    manager, library = make_manager(tmp_path)

    report = manager.download(str(document))

    entry = library.entry(report.completed[0].job.ref)
    assert stored_record(library, entry.path)["source_document"] == document.as_posix()


def test_a_manager_can_be_composed_with_its_own_collaborators(tmp_path: Path) -> None:
    registry = ProviderRegistry([make_provider()])
    library = Library(tmp_path / "library")
    manager = DownloadManager(
        registry,
        library,
        sources=SourceResolver(),
        planner=DownloadPlanner(registry),
    )

    assert manager.library is library
    assert manager.download(FILE_URL).succeeded is True
