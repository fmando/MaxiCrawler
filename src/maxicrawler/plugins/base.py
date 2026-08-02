"""Plugin extension protocols."""

from typing import Protocol


class Plugin(Protocol):
    """A named MaxiCrawler extension loaded from a package entry point.

    This is the distribution-level lifecycle contract used by
    :class:`~maxicrawler.plugins.loader.PluginLoader`. Its ``register`` hook is
    where a distribution adds its
    :class:`~maxicrawler.plugins.protocol.CrawlerPlugin` implementations to a
    :class:`~maxicrawler.plugins.registry.PluginRegistry`.
    """

    name: str

    def register(self) -> None: ...
