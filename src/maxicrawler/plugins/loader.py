"""Discovery for installed MaxiCrawler plugins."""

from collections.abc import Mapping
from importlib.metadata import EntryPoint, entry_points
from typing import cast

from maxicrawler.plugins.base import Plugin

PLUGIN_GROUP = "maxicrawler.plugins"


class PluginLoader:
    """Loads plugins registered through Python package entry points."""

    def load(self) -> list[Plugin]:
        """Instantiate and register every plugin in the MaxiCrawler group."""
        plugins: list[Plugin] = []
        for entry_point in self._entry_points():
            plugin = entry_point.load()()
            plugin.register()
            plugins.append(plugin)
        return plugins

    @staticmethod
    def _entry_points() -> list[EntryPoint]:
        """Return entry points in a shape stable across supported Python versions."""
        discovered = entry_points()
        if hasattr(discovered, "select"):
            return list(discovered.select(group=PLUGIN_GROUP))
        legacy_groups = cast(Mapping[str, list[EntryPoint]], discovered)
        return legacy_groups.get(PLUGIN_GROUP, [])
