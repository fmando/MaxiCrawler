"""Reusable plugin and provider test doubles."""

from maxicrawler.domain import (
    Availability,
    PluginCapability,
    PluginInfo,
    ProviderCapability,
    ProviderInfo,
    ResourceInspection,
    ResourceKind,
    ResourceMetadata,
    ResourceRef,
    ResourceSecret,
    UrlCategory,
    UrlClassification,
    UrlRecord,
)
from maxicrawler.providers.errors import UnsupportedResourceError


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


class StubProvider:
    """Configurable :class:`ResourceProvider` implementation for tests."""

    def __init__(
        self,
        name: str,
        *,
        priority: int = 0,
        supports: bool = True,
        url_prefix: str | None = None,
        kind: ResourceKind = ResourceKind.FILE,
        inspection: ResourceInspection | None = None,
        capabilities: frozenset[ProviderCapability] = frozenset({ProviderCapability.INSPECT}),
    ) -> None:
        self._metadata = ProviderInfo(
            name=name,
            version="1.0.0",
            module="tests.doubles",
            description=f"stub provider {name}",
            priority=priority,
            capabilities=capabilities,
        )
        self._supports = supports
        self._url_prefix = url_prefix
        self._kind = kind
        self._inspection = inspection
        self.inspected: list[ResourceRef] = []

    @property
    def metadata(self) -> ProviderInfo:
        return self._metadata

    def supports(self, classification: UrlClassification) -> bool:
        if self._url_prefix is not None:
            return classification.record.normalized_url.startswith(self._url_prefix)
        return self._supports

    def reference(self, classification: UrlClassification) -> ResourceRef:
        if not self.supports(classification):
            msg = f"unsupported classification: {classification.record.normalized_url}"
            raise UnsupportedResourceError(msg)
        key = classification.attribute("key")
        return ResourceRef(
            provider=self._metadata.name,
            resource_id=classification.attribute("handle") or "stub-handle",
            kind=self._kind,
            url=classification.record.normalized_url,
            secret=None if key is None else ResourceSecret(key),
        )

    def inspect(self, ref: ResourceRef) -> ResourceInspection:
        self.inspected.append(ref)
        if self._inspection is not None:
            return self._inspection
        return ResourceInspection(
            ref=ref,
            availability=Availability.AVAILABLE,
            metadata=ResourceMetadata(kind=self._kind, name="stub.bin", size=1024),
        )


class NotAProvider:
    """Object that deliberately fails the :class:`ResourceProvider` contract."""

    name = "broken"


def make_record(url: str) -> UrlRecord:
    """Return a :class:`UrlRecord` whose raw and normalized URL are *url*."""
    return UrlRecord(raw_url=url, normalized_url=url)


def make_ref(
    resource_id: str = "AaBbCcDd",
    *,
    provider: str = "mega",
    kind: ResourceKind = ResourceKind.FILE,
    parent_id: str | None = None,
    secret: str | None = None,
    url: str | None = None,
) -> ResourceRef:
    """Return a :class:`ResourceRef` without running a provider."""
    return ResourceRef(
        provider=provider,
        resource_id=resource_id,
        kind=kind,
        url=url if url is not None else f"https://mega.nz/file/{resource_id}",
        secret=None if secret is None else ResourceSecret(secret),
        parent_id=parent_id,
    )


def make_classification(
    url: str,
    *,
    plugin_name: str = "stub",
    category: UrlCategory = UrlCategory.FILE,
) -> UrlClassification:
    """Return a classification for *url* without running a plugin."""
    return UrlClassification(record=make_record(url), category=category, plugin_name=plugin_name)
