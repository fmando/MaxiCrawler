"""Tests for the plugin registry."""

import pytest
from doubles import NotAPlugin, StubPlugin, make_record

from maxicrawler.domain import PluginCapability
from maxicrawler.events import EventBus, PluginLoaded, PluginUnloaded
from maxicrawler.plugins import (
    DuplicatePluginError,
    InvalidPluginError,
    PluginRegistry,
    UnknownPluginError,
)


def test_register_returns_metadata_and_stores_plugin() -> None:
    registry = PluginRegistry()
    plugin = StubPlugin("alpha")

    info = registry.register(plugin)

    assert info.name == "alpha"
    assert "alpha" in registry
    assert len(registry) == 1
    assert registry.get("alpha") is plugin


def test_constructor_registers_supplied_plugins() -> None:
    registry = PluginRegistry([StubPlugin("alpha"), StubPlugin("beta")])

    assert len(registry) == 2
    assert {info.name for info in registry.discover()} == {"alpha", "beta"}


def test_register_rejects_duplicate_names() -> None:
    registry = PluginRegistry([StubPlugin("alpha")])

    with pytest.raises(DuplicatePluginError):
        registry.register(StubPlugin("alpha"))

    assert len(registry) == 1


def test_register_rejects_objects_violating_the_protocol() -> None:
    registry = PluginRegistry()

    with pytest.raises(InvalidPluginError):
        registry.register(NotAPlugin())  # type: ignore[arg-type]

    assert len(registry) == 0


def test_unregister_removes_plugin_and_returns_metadata() -> None:
    registry = PluginRegistry([StubPlugin("alpha")])

    info = registry.unregister("alpha")

    assert info.name == "alpha"
    assert "alpha" not in registry
    assert len(registry) == 0


def test_unregister_unknown_plugin_raises() -> None:
    registry = PluginRegistry()

    with pytest.raises(UnknownPluginError):
        registry.unregister("missing")


def test_get_and_metadata_raise_for_unknown_plugin() -> None:
    registry = PluginRegistry()

    with pytest.raises(UnknownPluginError):
        registry.get("missing")
    with pytest.raises(UnknownPluginError):
        registry.metadata("missing")


def test_metadata_returns_the_plugin_descriptor() -> None:
    registry = PluginRegistry([StubPlugin("alpha", priority=7)])

    info = registry.metadata("alpha")

    assert info.version == "1.0.0"
    assert info.priority == 7
    assert info.description == "stub plugin alpha"


def test_discover_orders_by_descending_priority_and_keeps_ties_stable() -> None:
    registry = PluginRegistry(
        [
            StubPlugin("low", priority=-10),
            StubPlugin("first-tie", priority=5),
            StubPlugin("high", priority=50),
            StubPlugin("second-tie", priority=5),
        ]
    )

    assert [info.name for info in registry.discover()] == [
        "high",
        "first-tie",
        "second-tie",
        "low",
    ]


def test_resolve_returns_highest_priority_plugin_that_claims_the_record() -> None:
    fallback = StubPlugin("fallback", priority=-100)
    specific = StubPlugin("specific", priority=10, url_prefix="https://example.test/")
    registry = PluginRegistry([fallback, specific])

    assert registry.resolve(make_record("https://example.test/a")) is specific
    assert registry.resolve(make_record("https://other.test/a")) is fallback


def test_resolve_returns_none_when_no_plugin_claims_the_record() -> None:
    registry = PluginRegistry([StubPlugin("alpha", handles=False)])

    assert registry.resolve(make_record("https://example.test/")) is None


def test_resolve_returns_none_for_an_empty_registry() -> None:
    assert PluginRegistry().resolve(make_record("https://example.test/")) is None


def test_with_capability_filters_metadata() -> None:
    registry = PluginRegistry(
        [
            StubPlugin("classifier", capabilities=frozenset({PluginCapability.CLASSIFY})),
            StubPlugin("downloader", capabilities=frozenset({PluginCapability.DOWNLOAD})),
        ]
    )

    names = [info.name for info in registry.with_capability(PluginCapability.DOWNLOAD)]

    assert names == ["downloader"]


def test_registration_publishes_lifecycle_events() -> None:
    bus = EventBus()
    events: list[object] = []
    bus.subscribe(PluginLoaded, events.append)
    bus.subscribe(PluginUnloaded, events.append)
    registry = PluginRegistry(event_bus=bus)

    registry.register(StubPlugin("alpha"))
    registry.unregister("alpha")

    assert [type(event) for event in events] == [PluginLoaded, PluginUnloaded]
    assert [event.plugin.name for event in events] == ["alpha", "alpha"]  # type: ignore[attr-defined]


def test_registry_without_event_bus_publishes_nothing() -> None:
    registry = PluginRegistry()

    registry.register(StubPlugin("alpha"))
    registry.unregister("alpha")

    assert len(registry) == 0


def test_iteration_yields_plugins_in_resolution_order() -> None:
    registry = PluginRegistry([StubPlugin("low", priority=0), StubPlugin("high", priority=9)])

    assert [plugin.metadata.name for plugin in registry] == ["high", "low"]
