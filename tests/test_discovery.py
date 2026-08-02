"""Tests for the in-memory discovery pipeline."""

from datetime import UTC, datetime

from maxicrawler.crawler import DiscoveryPipeline
from maxicrawler.domain import ScanSession
from maxicrawler.events import EventBus, ScanFinished, ScanStarted, UrlDiscovered


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
