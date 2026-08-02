"""In-memory discovery pipeline; it performs no network access."""

from maxicrawler.domain import DiscoveryResult, ScanSession, Statistics, UrlRecord
from maxicrawler.events import EventBus, ScanFinished, ScanStarted, UrlDiscovered
from maxicrawler.utils.urls import DuplicateDetector, normalize_url


class DiscoveryPipeline:
    """Normalizes and deduplicates URL candidates while publishing domain events."""

    def __init__(self, event_bus: EventBus, detector: DuplicateDetector | None = None) -> None:
        self._event_bus = event_bus
        self._detector = detector or DuplicateDetector()
        self._statistics = Statistics()

    @property
    def statistics(self) -> Statistics:
        """Return the current immutable discovery counters."""
        return self._statistics

    def start(self, session: ScanSession) -> None:
        """Publish the beginning of a caller-managed discovery session."""
        self._event_bus.publish(ScanStarted(session))

    def discover(self, raw_url: str, source_url: str | None = None) -> DiscoveryResult:
        """Process one URL candidate without fetching it."""
        record = UrlRecord(
            raw_url=raw_url, normalized_url=normalize_url(raw_url), source_url=source_url
        )
        duplicate = self._detector.register(record.normalized_url)
        self._statistics = self._statistics.with_discovery(duplicate=duplicate)
        result = DiscoveryResult(record=record, is_duplicate=duplicate)
        if not duplicate:
            self._event_bus.publish(UrlDiscovered(record))
        return result

    def finish(self, session: ScanSession) -> Statistics:
        """Publish completion and return the immutable session statistics."""
        self._event_bus.publish(ScanFinished(session, self._statistics))
        return self._statistics
