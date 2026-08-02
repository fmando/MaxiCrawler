"""Composition helper assembling the built-in plugin set.

Keeping this wiring in its own module lets :mod:`maxicrawler.plugins.registry`
stay unaware of any concrete plugin implementation.
"""

from maxicrawler.events import EventBus
from maxicrawler.plugins.generic import GenericPlugin
from maxicrawler.plugins.registry import PluginRegistry


def create_default_registry(*, event_bus: EventBus | None = None) -> PluginRegistry:
    """Return a registry pre-populated with MaxiCrawler's built-in plugins."""
    return PluginRegistry([GenericPlugin()], event_bus=event_bus)
