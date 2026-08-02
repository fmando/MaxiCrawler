"""End-to-end discovery over documents containing Mega share links."""

from datetime import UTC, datetime
from pathlib import Path

from maxicrawler.crawler import DiscoveryPipeline, LocalDiscoveryService
from maxicrawler.database import SQLiteDatabase, SQLiteDiscoveryRepository
from maxicrawler.documents import DocumentLoader
from maxicrawler.domain import ScanSession, UrlCategory, UrlRecord
from maxicrawler.events import EventBus
from maxicrawler.extractors import GenericUrlExtractor
from maxicrawler.plugins import PluginResolver, create_default_registry

DATA = Path(__file__).parent / "data" / "mega"
SESSION = ScanSession("mega-session", datetime(2026, 8, 2, 12, 0, tzinfo=UTC))


def run(tmp_path: Path) -> SQLiteDiscoveryRepository:
    """Run discovery over the Mega fixtures and return the repository."""
    repository = SQLiteDiscoveryRepository(SQLiteDatabase(tmp_path / "maxicrawler.db"))
    repository.initialize()
    service = LocalDiscoveryService(DiscoveryPipeline(EventBus()), repository=repository)
    service.run(DATA, SESSION)
    return repository


def test_discovery_routes_shares_to_mega_and_the_rest_to_generic() -> None:
    service = LocalDiscoveryService(DiscoveryPipeline(EventBus()))

    summary = service.run(DATA, SESSION)

    assert summary.documents_processed == 2
    assert summary.total_urls == 20
    assert summary.unique_urls == 19
    assert summary.duplicates_removed == 1
    assert [(usage.name, usage.count) for usage in summary.plugin_usage] == [
        ("mega", 13),
        ("generic", 6),
    ]


def test_a_share_repeated_in_two_documents_is_counted_once(tmp_path: Path) -> None:
    repository = run(tmp_path)

    backup = [
        stored
        for stored in repository.stored_urls(SESSION.session_id)
        if "AaBbCcDd" in stored.record.normalized_url
    ]

    assert len(backup) == 1, "the full backup link appears in both fixture documents"


def test_legacy_shares_that_differ_only_in_the_handle_stay_distinct(tmp_path: Path) -> None:
    repository = run(tmp_path)
    stored = repository.stored_urls(SESSION.session_id)

    legacy = {
        entry.record.normalized_url
        for entry in stored
        if entry.record.normalized_url.startswith("https://mega.nz/#!")
    }

    assert len(legacy) >= 3, "dropping the fragment would collapse these into one URL"


def test_file_and_folder_shares_are_stored_with_different_categories(tmp_path: Path) -> None:
    repository = run(tmp_path)
    stored = repository.stored_urls(SESSION.session_id)

    mega = [entry for entry in stored if entry.plugin_name == "mega"]

    assert {entry.category for entry in mega} == {
        str(UrlCategory.FILE),
        str(UrlCategory.CONTAINER),
    }


def test_non_share_pages_on_the_mega_host_are_stored_as_generic(tmp_path: Path) -> None:
    repository = run(tmp_path)

    pages = [
        entry
        for entry in repository.stored_urls(SESSION.session_id)
        if entry.record.normalized_url in {"https://mega.nz/pro", "https://mega.nz/about"}
    ]

    assert len(pages) == 2
    assert {entry.plugin_name for entry in pages} == {"generic"}
    assert {entry.category for entry in pages} == {str(UrlCategory.GENERIC)}


def test_a_malformed_share_falls_through_to_the_generic_plugin(tmp_path: Path) -> None:
    repository = run(tmp_path)

    short_handle = [
        entry
        for entry in repository.stored_urls(SESSION.session_id)
        if entry.record.normalized_url == "https://mega.nz/file/AaBbCc"
    ]

    assert len(short_handle) == 1
    assert short_handle[0].plugin_name == "generic"


def test_a_bare_legacy_marker_survives_extraction_as_a_generic_url(tmp_path: Path) -> None:
    # The extractor strips trailing "!" as prose punctuation, so "https://mega.nz/#!"
    # arrives as "https://mega.nz/#". No valid share link ends in "!", so this only
    # affects malformed input, which the generic plugin then handles.
    repository = run(tmp_path)

    entries = [
        entry
        for entry in repository.stored_urls(SESSION.session_id)
        if entry.record.normalized_url == "https://mega.nz/"
    ]

    assert len(entries) == 1
    assert entries[0].plugin_name == "generic"


def test_every_supported_mega_form_appears_in_the_fixtures() -> None:
    raw_urls = [
        candidate.raw_url
        for document in DocumentLoader().load_all(DATA)
        for candidate in GenericUrlExtractor().extract(document)
    ]

    assert any(url.startswith("https://mega.nz/file/") for url in raw_urls)
    assert any(url.startswith("https://mega.nz/folder/") for url in raw_urls)
    assert any("/file/N0d3H4nd" in url for url in raw_urls)
    assert any(url.startswith("https://mega.nz/#!") for url in raw_urls)
    assert any(url.startswith("https://mega.nz/#F!") for url in raw_urls)
    assert any(url.startswith("https://mega.co.nz/") for url in raw_urls)
    assert any(url.startswith("https://www.mega.nz/") for url in raw_urls)


def test_keys_survive_the_journey_from_document_to_classification() -> None:
    resolver = PluginResolver(create_default_registry())
    records = [
        UrlRecord(raw_url=candidate.raw_url, normalized_url=candidate.normalized_url)
        for document in DocumentLoader().load_all(DATA)
        for candidate in GenericUrlExtractor().extract(document)
    ]

    keys = {
        resolution.classification.attribute("key")
        for resolution in resolver.resolve_many(records)
        if resolution.classification is not None and resolution.classification.plugin_name == "mega"
    }

    assert "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG" in keys
    assert "0123456789abcdefghijkl" in keys
    assert None in keys, "the share published without a key is still recognized"
