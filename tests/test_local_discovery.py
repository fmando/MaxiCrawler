"""Tests for the offline discovery workflow."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from doubles import StubPlugin

from maxicrawler.crawler import DiscoveryPipeline, LocalDiscoveryService
from maxicrawler.database import SQLiteDatabase, SQLiteDiscoveryRepository
from maxicrawler.documents import DocumentLoader, TextDocumentReader
from maxicrawler.domain import ScanSession, UrlCategory
from maxicrawler.events import EventBus, ScanFinished, ScanStarted, UrlDiscovered
from maxicrawler.plugins import PluginRegistry

DATA = Path(__file__).parent / "data"
SESSION = ScanSession("session-1", datetime(2026, 8, 2, 12, 0, tzinfo=UTC))


def make_service(**kwargs: object) -> LocalDiscoveryService:
    """Return a service backed by a fresh pipeline and the default plugins."""
    pipeline = DiscoveryPipeline(EventBus())
    return LocalDiscoveryService(pipeline, **kwargs)  # type: ignore[arg-type]


def test_run_over_the_sample_directory_reports_consistent_counters() -> None:
    summary = make_service().run(DATA, SESSION)

    assert summary.documents_processed == 4
    assert summary.total_urls == summary.unique_urls + summary.duplicates_removed
    assert summary.duplicates_removed > 0
    assert summary.session is SESSION


def test_run_uses_the_generic_plugin_for_every_unique_url() -> None:
    summary = make_service().run(DATA, SESSION)

    assert [usage.name for usage in summary.plugin_usage] == ["generic"]
    assert summary.plugin_usage[0].count == summary.unique_urls


def test_run_over_a_single_file() -> None:
    summary = make_service().run(DATA / "release-notes.txt", SESSION)

    assert summary.documents_processed == 1
    assert summary.unique_urls == 5
    assert summary.duplicates_removed == 0


def test_duplicates_across_documents_are_counted_not_discarded_silently() -> None:
    summary = make_service().run(DATA, SESSION)

    # index.html and nested/notes.md both link to the plugin guide.
    assert summary.duplicates_removed >= 1
    assert summary.total_urls > summary.unique_urls


def test_unsupported_files_contribute_nothing() -> None:
    summary = make_service().run(DATA / "nested", SESSION)

    assert summary.documents_processed == 1
    assert all("never.example.test" not in usage.name for usage in summary.plugin_usage)


def test_run_publishes_the_session_lifecycle() -> None:
    bus = EventBus()
    events: list[object] = []
    for event_type in (ScanStarted, UrlDiscovered, ScanFinished):
        bus.subscribe(event_type, events.append)
    service = LocalDiscoveryService(DiscoveryPipeline(bus))

    service.run(DATA / "release-notes.txt", SESSION)

    assert type(events[0]) is ScanStarted
    assert type(events[-1]) is ScanFinished
    assert all(type(event) is UrlDiscovered for event in events[1:-1])


def test_run_does_not_bypass_the_plugin_registry() -> None:
    plugin = StubPlugin("stub", category=UrlCategory.CONTAINER)
    pipeline = DiscoveryPipeline(EventBus(), registry=PluginRegistry([plugin]))
    service = LocalDiscoveryService(pipeline)

    summary = service.run(DATA / "release-notes.txt", SESSION)

    assert [usage.name for usage in summary.plugin_usage] == ["stub"]
    assert len(plugin.classified) == summary.unique_urls


def test_run_without_any_plugin_reports_unresolved_urls() -> None:
    pipeline = DiscoveryPipeline(EventBus(), registry=PluginRegistry())
    service = LocalDiscoveryService(pipeline)

    summary = service.run(DATA / "release-notes.txt", SESSION)

    assert summary.plugin_usage == ()
    assert summary.statistics.unresolved_urls == summary.unique_urls


def test_run_records_the_source_document_of_each_url(tmp_path: Path) -> None:
    repository = SQLiteDiscoveryRepository(SQLiteDatabase(tmp_path / "maxicrawler.db"))
    repository.initialize()
    service = make_service(repository=repository)

    service.run(DATA / "release-notes.txt", SESSION)

    stored = repository.stored_urls(SESSION.session_id)
    assert stored
    assert all(entry.record.source_url is not None for entry in stored)
    assert all(entry.record.source_url.endswith("release-notes.txt") for entry in stored)  # type: ignore[union-attr]


def test_run_persists_unique_urls_and_session_counters(tmp_path: Path) -> None:
    repository = SQLiteDiscoveryRepository(SQLiteDatabase(tmp_path / "maxicrawler.db"))
    repository.initialize()
    service = make_service(repository=repository)

    summary = service.run(DATA, SESSION)

    stored_session = repository.stored_session(SESSION.session_id)
    assert stored_session is not None
    assert stored_session.finished_at is not None
    assert stored_session.statistics == summary.statistics
    assert len(repository.stored_urls(SESSION.session_id)) == summary.unique_urls


def test_run_persists_plugin_metadata(tmp_path: Path) -> None:
    repository = SQLiteDiscoveryRepository(SQLiteDatabase(tmp_path / "maxicrawler.db"))
    repository.initialize()
    service = make_service(repository=repository)

    service.run(DATA / "release-notes.txt", SESSION)

    stored = repository.stored_urls(SESSION.session_id)
    assert {entry.plugin_name for entry in stored} == {"generic"}
    assert {entry.category for entry in stored} == {"generic"}


def test_run_without_a_repository_still_produces_a_summary() -> None:
    summary = make_service().run(DATA / "release-notes.txt", SESSION)

    assert summary.unique_urls == 5


def test_run_accepts_a_restricted_loader() -> None:
    service = make_service(loader=DocumentLoader([TextDocumentReader()]))

    summary = service.run(DATA, SESSION)

    assert summary.documents_processed == 1


def test_run_over_an_empty_directory(tmp_path: Path) -> None:
    summary = make_service().run(tmp_path, SESSION)

    assert summary.documents_processed == 0
    assert summary.total_urls == 0
    assert summary.plugin_usage == ()


def test_run_raises_for_a_missing_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        make_service().run(tmp_path / "missing", SESSION)


def test_service_exposes_its_collaborators() -> None:
    pipeline = DiscoveryPipeline(EventBus())
    loader = DocumentLoader()
    service = LocalDiscoveryService(pipeline, loader=loader)

    assert service.pipeline is pipeline
    assert service.loader is loader
