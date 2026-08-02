"""The public protocol implemented by URL-handling MaxiCrawler plugins."""

from typing import Protocol, runtime_checkable

from maxicrawler.domain import PluginInfo, UrlClassification, UrlRecord


@runtime_checkable
class CrawlerPlugin(Protocol):
    """Decides whether it is responsible for a URL and classifies it.

    Implementations receive immutable domain objects and return immutable
    domain objects. A plugin must stay infrastructure-independent: network
    access, persistence, and file-system I/O belong to the infrastructure
    layer and to later sprints, never to this protocol.

    Implementations are duck-typed; inheriting from this protocol is optional
    but makes the contract explicit to readers and type checkers.
    """

    @property
    def metadata(self) -> PluginInfo:
        """Return the immutable descriptor advertised by this plugin."""
        ...

    def can_handle(self, record: UrlRecord) -> bool:
        """Return whether this plugin claims responsibility for *record*.

        Implementations must be side-effect free and must not perform I/O.
        """
        ...

    def classify(self, record: UrlRecord) -> UrlClassification:
        """Return this plugin's verdict about *record*.

        Callers may invoke this for records the plugin does not handle;
        implementations should then report :attr:`UrlCategory.UNSUPPORTED`
        rather than raising.
        """
        ...
