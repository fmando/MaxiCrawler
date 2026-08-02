"""Composition helper assembling the built-in plugin set.

Keeping this wiring in its own module lets :mod:`maxicrawler.plugins.registry`
stay unaware of any concrete plugin implementation.
"""

from maxicrawler.events import EventBus
from maxicrawler.plugins.generic import GenericPlugin
from maxicrawler.plugins.mega import MegaPlugin
from maxicrawler.plugins.registry import PluginRegistry


def create_default_registry(*, event_bus: EventBus | None = None) -> PluginRegistry:
    """Return a registry pre-populated with MaxiCrawler's built-in plugins.

    Provider plugins are registered above :class:`GenericPlugin`, which stays
    the lowest-priority fallback. Callers that want a different set can build a
    :class:`PluginRegistry` directly.
    """
    return PluginRegistry([MegaPlugin(), GenericPlugin()], event_bus=event_bus)
