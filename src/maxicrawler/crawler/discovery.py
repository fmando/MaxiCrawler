"""In-memory discovery pipeline; it performs no network access."""

from maxicrawler.domain import DiscoveryResult, ScanSession, Statistics, UrlRecord
from maxicrawler.events import EventBus, ScanFinished, ScanStarted, UrlDiscovered
from maxicrawler.plugins import PluginRegistry, PluginResolver, create_default_registry
from maxicrawler.utils.urls import DuplicateDetector, normalize_url


class DiscoveryPipeline:
    """Normalizes, deduplicates, and plugin-resolves URL candidates.

    The pipeline delegates every URL-specific decision to the plugins held by
    its :class:`PluginRegistry`, so adding support for a host means registering
    a plugin rather than changing this class.
    """

    def __init__(
        self,
        event_bus: EventBus,
        detector: DuplicateDetector | None = None,
        *,
        registry: PluginRegistry | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._detector = detector or DuplicateDetector()
        self._registry = registry if registry is not None else create_default_registry()
        self._resolver = PluginResolver(self._registry)
        self._statistics = Statistics()

    @property
    def statistics(self) -> Statistics:
        """Return the current immutable discovery counters."""
        return self._statistics

    @property
    def registry(self) -> PluginRegistry:
        """Return the registry used to resolve discovered URLs."""
        return self._registry

    def start(self, session: ScanSession) -> None:
        """Publish the beginning of a caller-managed discovery session."""
        self._event_bus.publish(ScanStarted(session))

    def record_document(self) -> None:
        """Count one processed source document.

        The pipeline does not read documents itself; callers report each
        document they fed in so the session counters stay in one place.
        """
        self._statistics = self._statistics.with_document()

    def discover(self, raw_url: str, source_url: str | None = None) -> DiscoveryResult:
        """Process one URL candidate without fetching it.

        Unique candidates are resolved against the plugin registry; duplicates
        are neither resolved nor announced again, so their result carries no
        resolution.
        """
        record = UrlRecord(
            raw_url=raw_url, normalized_url=normalize_url(raw_url), source_url=source_url
        )
        duplicate = self._detector.register(record.normalized_url)
        resolution = None if duplicate else self._resolver.resolve(record)
        self._statistics = self._statistics.with_discovery(
            duplicate=duplicate,
            resolved=resolution is None or resolution.is_resolved,
        )
        result = DiscoveryResult(record=record, is_duplicate=duplicate, resolution=resolution)
        if not duplicate:
            self._event_bus.publish(UrlDiscovered(record))
        return result

    def finish(self, session: ScanSession) -> Statistics:
        """Publish completion and return the immutable session statistics."""
        self._event_bus.publish(ScanFinished(session, self._statistics))
        return self._statistics
