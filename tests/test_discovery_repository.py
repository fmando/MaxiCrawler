"""Tests for the discovery persistence port and its SQLite adapter."""

from datetime import UTC, datetime
from pathlib import Path

from maxicrawler.crawler import DiscoveryRepository, NullDiscoveryRepository
from maxicrawler.database import SQLiteDatabase, SQLiteDiscoveryRepository
from maxicrawler.domain import (
    DiscoveryResult,
    PluginInfo,
    PluginResolution,
    ScanSession,
    Statistics,
    UrlCategory,
    UrlClassification,
    UrlRecord,
)

SESSION = ScanSession("session-1", datetime(2026, 8, 2, 12, 0, tzinfo=UTC))


def make_repository(tmp_path: Path) -> SQLiteDiscoveryRepository:
    """Return an initialized repository backed by a temporary database."""
    repository = SQLiteDiscoveryRepository(SQLiteDatabase(tmp_path / "maxicrawler.db"))
    repository.initialize()
    return repository


def make_result(url: str, *, resolved: bool = True, duplicate: bool = False) -> DiscoveryResult:
    """Return a discovery result for *url*."""
    record = UrlRecord(raw_url=url, normalized_url=url, source_url="docs/index.html")
    if not resolved:
        return DiscoveryResult(record=record, is_duplicate=duplicate)
    info = PluginInfo(name="generic", version="0.1.0", module="tests")
    resolution = PluginResolution(
        record=record,
        plugin=info,
        classification=UrlClassification(record, UrlCategory.GENERIC, "generic"),
    )
    return DiscoveryResult(record=record, is_duplicate=duplicate, resolution=resolution)


def test_adapter_satisfies_the_repository_protocol(tmp_path: Path) -> None:
    assert isinstance(make_repository(tmp_path), DiscoveryRepository)


def test_null_repository_satisfies_the_protocol() -> None:
    assert isinstance(NullDiscoveryRepository(), DiscoveryRepository)


def test_null_repository_discards_everything() -> None:
    repository = NullDiscoveryRepository()

    repository.start_session(SESSION)
    repository.save_result(SESSION, make_result("https://example.test/a"))
    repository.finish_session(SESSION, Statistics())


def test_initialize_is_idempotent(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)

    repository.initialize()

    assert repository.stored_session(SESSION.session_id) is None


def test_session_is_stored_and_completed(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    statistics = Statistics(documents_processed=3, discovered_urls=7, duplicate_urls=2)

    repository.start_session(SESSION)
    started = repository.stored_session(SESSION.session_id)
    repository.finish_session(SESSION, statistics)
    finished = repository.stored_session(SESSION.session_id)

    assert started is not None
    assert started.started_at == SESSION.started_at
    assert started.finished_at is None
    assert finished is not None
    assert finished.finished_at is not None
    assert finished.statistics == statistics


def test_results_are_stored_with_plugin_metadata(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    repository.start_session(SESSION)

    repository.save_result(SESSION, make_result("https://example.test/a"))

    stored = repository.stored_urls(SESSION.session_id)
    assert len(stored) == 1
    assert stored[0].record.normalized_url == "https://example.test/a"
    assert stored[0].record.source_url == "docs/index.html"
    assert stored[0].plugin_name == "generic"
    assert stored[0].category == "generic"


def test_unresolved_results_are_stored_without_plugin_metadata(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    repository.start_session(SESSION)

    repository.save_result(SESSION, make_result("https://example.test/a", resolved=False))

    stored = repository.stored_urls(SESSION.session_id)
    assert stored[0].plugin_name is None
    assert stored[0].category is None


def test_saving_the_same_url_twice_is_a_no_op(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    repository.start_session(SESSION)

    repository.save_result(SESSION, make_result("https://example.test/a"))
    repository.save_result(SESSION, make_result("https://example.test/a"))

    assert len(repository.stored_urls(SESSION.session_id)) == 1


def test_results_keep_insertion_order(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    repository.start_session(SESSION)

    for url in ("https://example.test/c", "https://example.test/a", "https://example.test/b"):
        repository.save_result(SESSION, make_result(url))

    assert [
        stored.record.normalized_url for stored in repository.stored_urls(SESSION.session_id)
    ] == [
        "https://example.test/c",
        "https://example.test/a",
        "https://example.test/b",
    ]


def test_sessions_are_isolated_from_each_other(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    other = ScanSession("session-2", datetime(2026, 8, 3, tzinfo=UTC))
    repository.start_session(SESSION)
    repository.start_session(other)

    repository.save_result(SESSION, make_result("https://example.test/a"))
    repository.save_result(other, make_result("https://example.test/a"))

    assert len(repository.stored_urls(SESSION.session_id)) == 1
    assert len(repository.stored_urls(other.session_id)) == 1


def test_restarting_a_session_clears_its_completion(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    repository.start_session(SESSION)
    repository.finish_session(SESSION, Statistics(discovered_urls=1))

    repository.start_session(SESSION)

    stored = repository.stored_session(SESSION.session_id)
    assert stored is not None
    assert stored.finished_at is None


def test_unknown_session_returns_none(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)

    assert repository.stored_session("missing") is None
    assert repository.stored_urls("missing") == ()


def test_repository_exposes_its_database(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "maxicrawler.db")

    assert SQLiteDiscoveryRepository(database).database is database
