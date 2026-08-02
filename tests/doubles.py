"""Reusable plugin test doubles."""

from maxicrawler.domain import (
    PluginCapability,
    PluginInfo,
    UrlCategory,
    UrlClassification,
    UrlRecord,
)


class StubPlugin:
    """Configurable :class:`CrawlerPlugin` implementation for tests."""

    def __init__(
        self,
        name: str,
        *,
        priority: int = 0,
        handles: bool = True,
        url_prefix: str | None = None,
        category: UrlCategory = UrlCategory.FILE,
        capabilities: frozenset[PluginCapability] = frozenset({PluginCapability.CLASSIFY}),
    ) -> None:
        self._metadata = PluginInfo(
            name=name,
            version="1.0.0",
            module="tests.doubles",
            description=f"stub plugin {name}",
            priority=priority,
            capabilities=capabilities,
        )
        self._handles = handles
        self._url_prefix = url_prefix
        self._category = category
        self.classified: list[UrlRecord] = []

    @property
    def metadata(self) -> PluginInfo:
        return self._metadata

    def can_handle(self, record: UrlRecord) -> bool:
        if self._url_prefix is not None:
            return record.normalized_url.startswith(self._url_prefix)
        return self._handles

    def classify(self, record: UrlRecord) -> UrlClassification:
        self.classified.append(record)
        return UrlClassification(
            record=record,
            category=self._category,
            plugin_name=self._metadata.name,
        )


class NotAPlugin:
    """Object that deliberately fails the :class:`CrawlerPlugin` contract."""

    name = "broken"


def make_record(url: str) -> UrlRecord:
    """Return a :class:`UrlRecord` whose raw and normalized URL are *url*."""
    return UrlRecord(raw_url=url, normalized_url=url)
