"""Tests for the plugin resolver."""

from doubles import StubPlugin, make_record

from maxicrawler.domain import UrlCategory
from maxicrawler.plugins import PluginRegistry, PluginResolver, create_default_registry


def test_resolve_returns_plugin_metadata_and_classification() -> None:
    resolver = PluginResolver(create_default_registry())
    record = make_record("https://example.test/docs")

    resolution = resolver.resolve(record)

    assert resolution.is_resolved is True
    assert resolution.record is record
    assert resolution.plugin is not None
    assert resolution.plugin.name == "generic"
    assert resolution.classification is not None
    assert resolution.classification.category is UrlCategory.GENERIC
    assert resolution.classification.plugin_name == "generic"


def test_resolve_reports_an_unresolved_record() -> None:
    resolver = PluginResolver(PluginRegistry())
    record = make_record("https://example.test/docs")

    resolution = resolver.resolve(record)

    assert resolution.is_resolved is False
    assert resolution.plugin is None
    assert resolution.classification is None
    assert resolution.record is record


def test_resolve_delegates_classification_to_the_selected_plugin() -> None:
    plugin = StubPlugin("stub", category=UrlCategory.CONTAINER)
    resolver = PluginResolver(PluginRegistry([plugin]))
    record = make_record("https://example.test/album")

    resolution = resolver.resolve(record)

    assert plugin.classified == [record]
    assert resolution.classification is not None
    assert resolution.classification.category is UrlCategory.CONTAINER


def test_resolve_prefers_the_highest_priority_plugin() -> None:
    fallback = StubPlugin("fallback", priority=-100, category=UrlCategory.GENERIC)
    specific = StubPlugin("specific", priority=10, category=UrlCategory.FILE)
    resolver = PluginResolver(PluginRegistry([fallback, specific]))

    resolution = resolver.resolve(make_record("https://example.test/file.bin"))

    assert resolution.plugin is not None
    assert resolution.plugin.name == "specific"
    assert fallback.classified == []


def test_resolve_many_preserves_input_order() -> None:
    resolver = PluginResolver(create_default_registry())
    records = [make_record(f"https://example.test/{index}") for index in range(3)]

    resolutions = resolver.resolve_many(records)

    assert [resolution.record for resolution in resolutions] == records
    assert all(resolution.is_resolved for resolution in resolutions)


def test_resolve_many_returns_an_empty_tuple_for_no_records() -> None:
    assert PluginResolver(create_default_registry()).resolve_many([]) == ()


def test_resolver_exposes_its_registry() -> None:
    registry = PluginRegistry()

    assert PluginResolver(registry).registry is registry
