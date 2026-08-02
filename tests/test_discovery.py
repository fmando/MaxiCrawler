"""Tests for the in-memory discovery pipeline."""

from datetime import UTC, datetime

from doubles import StubPlugin

from maxicrawler.crawler import DiscoveryPipeline
from maxicrawler.domain import ScanSession, UrlCategory
from maxicrawler.events import EventBus, ScanFinished, ScanStarted, UrlDiscovered
from maxicrawler.plugins import PluginRegistry


def test_discovery_pipeline_emits_lifecycle_and_unique_url_events() -> None:
    bus = EventBus()
    events: list[object] = []
    for event_type in (ScanStarted, UrlDiscovered, ScanFinished):
        bus.subscribe(event_type, events.append)
    pipeline = DiscoveryPipeline(bus)
    session = ScanSession("session-1", datetime(2026, 8, 2, tzinfo=UTC))

    pipeline.start(session)
    first = pipeline.discover("https://EXAMPLE.test:443/docs?b=2&a=1")
    second = pipeline.discover("https://example.test/docs?a=1&b=2")
    statistics = pipeline.finish(session)

    assert first.is_duplicate is False
    assert second.is_duplicate is True
    assert statistics.discovered_urls == 1
    assert statistics.duplicate_urls == 1
    assert [type(event) for event in events] == [ScanStarted, UrlDiscovered, ScanFinished]


def test_discovery_attaches_the_resolution_of_the_default_registry() -> None:
    pipeline = DiscoveryPipeline(EventBus())

    result = pipeline.discover("https://example.test/docs")

    assert result.resolution is not None
    assert result.resolution.is_resolved is True
    assert result.resolution.plugin is not None
    assert result.resolution.plugin.name == "generic"
    assert result.resolution.classification is not None
    assert result.resolution.classification.category is UrlCategory.GENERIC
    assert pipeline.statistics.unresolved_urls == 0


def test_duplicates_are_not_resolved_again() -> None:
    plugin = StubPlugin("stub")
    pipeline = DiscoveryPipeline(EventBus(), registry=PluginRegistry([plugin]))

    pipeline.discover("https://example.test/docs")
    duplicate = pipeline.discover("https://example.test/docs")

    assert duplicate.is_duplicate is True
    assert duplicate.resolution is None
    assert len(plugin.classified) == 1


def test_pipeline_uses_the_injected_registry() -> None:
    plugin = StubPlugin("stub", category=UrlCategory.FILE)
    registry = PluginRegistry([plugin])
    pipeline = DiscoveryPipeline(EventBus(), registry=registry)

    result = pipeline.discover("https://example.test/file.bin")

    assert pipeline.registry is registry
    assert result.resolution is not None
    assert result.resolution.plugin is not None
    assert result.resolution.plugin.name == "stub"
    assert result.resolution.classification is not None
    assert result.resolution.classification.category is UrlCategory.FILE


def test_unresolved_candidates_are_counted() -> None:
    pipeline = DiscoveryPipeline(EventBus(), registry=PluginRegistry())

    result = pipeline.discover("https://example.test/docs")

    assert result.resolution is not None
    assert result.resolution.is_resolved is False
    assert pipeline.statistics.discovered_urls == 1
    assert pipeline.statistics.unresolved_urls == 1


def test_registering_a_plugin_changes_later_resolutions() -> None:
    pipeline = DiscoveryPipeline(EventBus(), registry=PluginRegistry())

    before = pipeline.discover("https://example.test/first")
    pipeline.registry.register(StubPlugin("late"))
    after = pipeline.discover("https://example.test/second")

    assert before.resolution is not None
    assert before.resolution.is_resolved is False
    assert after.resolution is not None
    assert after.resolution.plugin is not None
    assert after.resolution.plugin.name == "late"
