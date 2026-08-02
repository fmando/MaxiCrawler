"""Registration, discovery, and lookup of MaxiCrawler plugins."""

from collections.abc import Iterable, Iterator

from maxicrawler.domain import PluginCapability, PluginInfo, UrlRecord
from maxicrawler.events import EventBus, PluginLoaded, PluginUnloaded
from maxicrawler.plugins.protocol import CrawlerPlugin


class PluginRegistryError(RuntimeError):
    """Base class for every plugin registry failure."""


class DuplicatePluginError(PluginRegistryError):
    """Raised when a plugin name is registered more than once."""


class UnknownPluginError(PluginRegistryError):
    """Raised when a plugin name is not registered."""


class InvalidPluginError(PluginRegistryError):
    """Raised when an object does not implement :class:`CrawlerPlugin`."""


class PluginRegistry:
    """Owns the active plugins and finds the one responsible for a URL.

    Plugins are ordered by descending :attr:`PluginInfo.priority`; plugins
    sharing a priority keep their registration order. Resolution is therefore
    deterministic, and specialised plugins can outrank generic fallbacks.

    Registration and removal publish :class:`PluginLoaded` and
    :class:`PluginUnloaded` when an event bus is supplied. The registry holds
    no infrastructure state and performs no I/O.
    """

    def __init__(
        self,
        plugins: Iterable[CrawlerPlugin] = (),
        *,
        event_bus: EventBus | None = None,
    ) -> None:
        self._plugins: dict[str, CrawlerPlugin] = {}
        self._event_bus = event_bus
        for plugin in plugins:
            self.register(plugin)

    def register(self, plugin: CrawlerPlugin) -> PluginInfo:
        """Add *plugin* to the registry and return its metadata.

        Raises:
            InvalidPluginError: *plugin* does not implement the protocol.
            DuplicatePluginError: a plugin with the same name is registered.
        """
        if not isinstance(plugin, CrawlerPlugin):
            msg = f"object does not implement CrawlerPlugin: {plugin!r}"
            raise InvalidPluginError(msg)
        info = plugin.metadata
        if info.name in self._plugins:
            msg = f"plugin already registered: {info.name}"
            raise DuplicatePluginError(msg)
        self._plugins[info.name] = plugin
        if self._event_bus is not None:
            self._event_bus.publish(PluginLoaded(info))
        return info

    def unregister(self, name: str) -> PluginInfo:
        """Remove the plugin called *name* and return its metadata.

        Raises:
            UnknownPluginError: no plugin is registered under *name*.
        """
        plugin = self._plugins.pop(name, None)
        if plugin is None:
            msg = f"plugin is not registered: {name}"
            raise UnknownPluginError(msg)
        info = plugin.metadata
        if self._event_bus is not None:
            self._event_bus.publish(PluginUnloaded(info))
        return info

    def discover(self) -> tuple[PluginInfo, ...]:
        """Return the metadata of every plugin in resolution order."""
        return tuple(plugin.metadata for plugin in self._ordered())

    def metadata(self, name: str) -> PluginInfo:
        """Return the metadata of the plugin called *name*.

        Raises:
            UnknownPluginError: no plugin is registered under *name*.
        """
        return self.get(name).metadata

    def get(self, name: str) -> CrawlerPlugin:
        """Return the plugin called *name*.

        Raises:
            UnknownPluginError: no plugin is registered under *name*.
        """
        plugin = self._plugins.get(name)
        if plugin is None:
            msg = f"plugin is not registered: {name}"
            raise UnknownPluginError(msg)
        return plugin

    def resolve(self, record: UrlRecord) -> CrawlerPlugin | None:
        """Return the highest-priority plugin claiming *record*, if any."""
        for plugin in self._ordered():
            if plugin.can_handle(record):
                return plugin
        return None

    def with_capability(self, capability: PluginCapability) -> tuple[PluginInfo, ...]:
        """Return the metadata of plugins advertising *capability*."""
        return tuple(info for info in self.discover() if info.supports(capability))

    def _ordered(self) -> tuple[CrawlerPlugin, ...]:
        """Return the plugins sorted by descending priority, ties kept stable."""
        return tuple(sorted(self._plugins.values(), key=lambda p: -p.metadata.priority))

    def __contains__(self, name: object) -> bool:
        """Return whether a plugin is registered under *name*."""
        return name in self._plugins

    def __iter__(self) -> Iterator[CrawlerPlugin]:
        """Iterate over the registered plugins in resolution order."""
        return iter(self._ordered())

    def __len__(self) -> int:
        """Return the number of registered plugins."""
        return len(self._plugins)
