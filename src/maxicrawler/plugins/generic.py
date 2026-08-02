"""The built-in fallback plugin for ordinary HTTP(S) URLs."""

from urllib.parse import urlsplit

from maxicrawler import __version__
from maxicrawler.domain import (
    PluginCapability,
    PluginInfo,
    UrlCategory,
    UrlClassification,
    UrlRecord,
)

GENERIC_PLUGIN_NAME = "generic"
"""Registry name of the built-in fallback plugin."""

GENERIC_PLUGIN_PRIORITY = -100
"""Default priority; deliberately below any specialised plugin."""

SUPPORTED_SCHEMES = frozenset({"http", "https"})
"""URL schemes the generic plugin recognises."""


class GenericPlugin:
    """Claims ordinary HTTP(S) URLs that no specialised plugin handles.

    The plugin is registered at the lowest priority so host-specific plugins
    added in later sprints outrank it. It inspects the normalized URL string
    only: it opens no connection, reads no file, and touches no database.
    """

    def __init__(self, *, priority: int = GENERIC_PLUGIN_PRIORITY) -> None:
        self._metadata = PluginInfo(
            name=GENERIC_PLUGIN_NAME,
            version=__version__,
            module=__name__,
            description="Fallback handler for ordinary HTTP and HTTPS URLs.",
            priority=priority,
            capabilities=frozenset({PluginCapability.CLASSIFY}),
        )

    @property
    def metadata(self) -> PluginInfo:
        """Return the immutable descriptor for this plugin."""
        return self._metadata

    def can_handle(self, record: UrlRecord) -> bool:
        """Return whether *record* is an absolute HTTP(S) URL with a host."""
        parsed = urlsplit(record.normalized_url)
        return parsed.scheme.lower() in SUPPORTED_SCHEMES and bool(parsed.hostname)

    def classify(self, record: UrlRecord) -> UrlClassification:
        """Classify *record* from its normalized URL, without any I/O."""
        category = UrlCategory.GENERIC if self.can_handle(record) else UrlCategory.UNSUPPORTED
        return UrlClassification(
            record=record,
            category=category,
            plugin_name=GENERIC_PLUGIN_NAME,
        )
