"""Immutable domain models describing plugins and their verdicts about URLs.

These value objects are the vocabulary the application layer and plugins share.
They depend on nothing but the standard library and :mod:`maxicrawler.domain.models`.
"""

from dataclasses import dataclass
from enum import StrEnum

from maxicrawler.domain.models import UrlRecord


class PluginCapability(StrEnum):
    """A coarse capability a plugin advertises to the application layer.

    Only :attr:`CLASSIFY` is exercised in this sprint; the remaining members
    describe the extension points later sprints will implement.
    """

    CLASSIFY = "classify"
    DISCOVER = "discover"
    DOWNLOAD = "download"


class UrlCategory(StrEnum):
    """The kind of resource a URL points at, as judged by a plugin."""

    GENERIC = "generic"
    """An ordinary web resource without specialised handling."""

    CONTAINER = "container"
    """A page that is expected to list further URLs."""

    FILE = "file"
    """A directly downloadable resource."""

    UNSUPPORTED = "unsupported"
    """A URL the plugin cannot process."""


@dataclass(frozen=True, slots=True)
class PluginInfo:
    """Describes a plugin without coupling callers to its implementation.

    ``priority`` orders plugins during resolution: higher values are asked
    first, so specialised plugins can outrank generic fallbacks.
    """

    name: str
    version: str
    module: str
    description: str = ""
    priority: int = 0
    capabilities: frozenset[PluginCapability] = frozenset()

    def supports(self, capability: PluginCapability) -> bool:
        """Return whether the plugin advertises *capability*."""
        return capability in self.capabilities


@dataclass(frozen=True, slots=True)
class LinkAttribute:
    """One piece of structured metadata a plugin read out of a URL.

    Attributes are intentionally plain strings: their meaning belongs to the
    plugin that produced them, and the domain stays free of provider knowledge.
    """

    name: str
    value: str


@dataclass(frozen=True, slots=True)
class UrlClassification:
    """A plugin's verdict about one URL, produced without any I/O.

    ``attributes`` carries provider-specific metadata, such as the public
    handle and decryption key of a file-hosting share. It is empty for plugins
    that only assign a category.
    """

    record: UrlRecord
    category: UrlCategory
    plugin_name: str
    attributes: tuple[LinkAttribute, ...] = ()

    @property
    def is_supported(self) -> bool:
        """Return whether the classifying plugin can process the URL."""
        return self.category is not UrlCategory.UNSUPPORTED

    def attribute(self, name: str) -> str | None:
        """Return the value of the attribute called *name*, if it is present."""
        for attribute in self.attributes:
            if attribute.name == name:
                return attribute.value
        return None


@dataclass(frozen=True, slots=True)
class PluginResolution:
    """The structured outcome of resolving the plugin responsible for a URL.

    Both ``plugin`` and ``classification`` are ``None`` when no registered
    plugin claimed the record.
    """

    record: UrlRecord
    plugin: PluginInfo | None = None
    classification: UrlClassification | None = None

    @property
    def is_resolved(self) -> bool:
        """Return whether a responsible plugin was found."""
        return self.plugin is not None
