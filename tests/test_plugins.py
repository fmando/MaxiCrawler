"""Tests for entry-point plugin loading."""

from typing import Any

from maxicrawler.plugins import PluginLoader


class ExamplePlugin:
    """Small test plugin."""

    name = "example"
    registered = False

    def register(self) -> None:
        self.registered = True


class FakeEntryPoint:
    """Entry point stand-in that returns the test plugin type."""

    def load(self) -> type[ExamplePlugin]:
        return ExamplePlugin


def test_plugin_loader_instantiates_and_registers_plugins(monkeypatch: Any) -> None:
    monkeypatch.setattr(PluginLoader, "_entry_points", staticmethod(lambda: [FakeEntryPoint()]))

    plugins = PluginLoader().load()

    assert len(plugins) == 1
    assert plugins[0].name == "example"
    assert plugins[0].registered is True
