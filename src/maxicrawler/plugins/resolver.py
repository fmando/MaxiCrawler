"""Resolution of the plugin responsible for a URL record."""

from collections.abc import Iterable

from maxicrawler.domain import PluginResolution, UrlRecord
from maxicrawler.plugins.registry import PluginRegistry


class PluginResolver:
    """Turns URL records into structured, immutable plugin resolutions.

    The resolver is the read-only application-facing view of a
    :class:`PluginRegistry`: it asks the registry for the responsible plugin
    and lets that plugin classify the record. It performs no I/O and never
    mutates the registry.
    """

    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    @property
    def registry(self) -> PluginRegistry:
        """Return the registry this resolver reads from."""
        return self._registry

    def resolve(self, record: UrlRecord) -> PluginResolution:
        """Return the responsible plugin and its classification for *record*.

        The returned resolution reports :attr:`PluginResolution.is_resolved`
        as ``False`` when no registered plugin claims the record.
        """
        plugin = self._registry.resolve(record)
        if plugin is None:
            return PluginResolution(record=record)
        return PluginResolution(
            record=record,
            plugin=plugin.metadata,
            classification=plugin.classify(record),
        )

    def resolve_many(self, records: Iterable[UrlRecord]) -> tuple[PluginResolution, ...]:
        """Resolve every record in *records*, preserving the input order."""
        return tuple(self.resolve(record) for record in records)
