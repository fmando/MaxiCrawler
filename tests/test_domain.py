"""Tests for immutable domain models."""

from dataclasses import FrozenInstanceError

import pytest

from maxicrawler.domain import (
    LinkAttribute,
    PluginCapability,
    PluginInfo,
    PluginResolution,
    Statistics,
    UrlCategory,
    UrlClassification,
    UrlRecord,
)


def test_url_record_is_immutable() -> None:
    record = UrlRecord("https://example.test", "https://example.test/")

    with pytest.raises(FrozenInstanceError):
        record.raw_url = "https://other.test"  # type: ignore[misc]


def test_statistics_returns_new_values() -> None:
    initial = Statistics()

    discovered = initial.with_discovery(duplicate=False)
    duplicate = discovered.with_discovery(duplicate=True)

    assert initial.discovered_urls == 0
    assert discovered.discovered_urls == 1
    assert duplicate.duplicate_urls == 1


def test_statistics_counts_unresolved_candidates() -> None:
    statistics = Statistics().with_discovery(duplicate=False, resolved=False)

    assert statistics.discovered_urls == 1
    assert statistics.unresolved_urls == 1


def test_statistics_ignores_resolution_state_for_duplicates() -> None:
    statistics = Statistics().with_discovery(duplicate=True, resolved=False)

    assert statistics.duplicate_urls == 1
    assert statistics.discovered_urls == 0
    assert statistics.unresolved_urls == 0


def test_plugin_info_reports_advertised_capabilities() -> None:
    info = PluginInfo(
        name="example",
        version="1.0.0",
        module="tests",
        capabilities=frozenset({PluginCapability.CLASSIFY}),
    )

    assert info.supports(PluginCapability.CLASSIFY) is True
    assert info.supports(PluginCapability.DOWNLOAD) is False
    assert info.priority == 0
    assert info.description == ""


def test_plugin_info_is_immutable() -> None:
    info = PluginInfo(name="example", version="1.0.0", module="tests")

    with pytest.raises(FrozenInstanceError):
        info.name = "other"  # type: ignore[misc]


def test_url_classification_reports_support() -> None:
    record = UrlRecord("https://example.test", "https://example.test/")
    supported = UrlClassification(record, UrlCategory.GENERIC, "generic")
    unsupported = UrlClassification(record, UrlCategory.UNSUPPORTED, "generic")

    assert supported.is_supported is True
    assert unsupported.is_supported is False


def test_url_classification_has_no_attributes_by_default() -> None:
    record = UrlRecord("https://example.test", "https://example.test/")

    classification = UrlClassification(record, UrlCategory.GENERIC, "generic")

    assert classification.attributes == ()
    assert classification.attribute("handle") is None


def test_url_classification_exposes_structured_attributes() -> None:
    record = UrlRecord("https://example.test", "https://example.test/")

    classification = UrlClassification(
        record,
        UrlCategory.FILE,
        "mega",
        attributes=(LinkAttribute("handle", "AaBbCcDd"), LinkAttribute("key", "secret")),
    )

    assert classification.attribute("handle") == "AaBbCcDd"
    assert classification.attribute("key") == "secret"
    assert classification.attribute("missing") is None


def test_url_classification_stays_hashable_with_attributes() -> None:
    record = UrlRecord("https://example.test", "https://example.test/")
    classification = UrlClassification(
        record, UrlCategory.FILE, "mega", attributes=(LinkAttribute("handle", "AaBbCcDd"),)
    )

    assert len({classification, classification}) == 1


def test_plugin_resolution_defaults_to_unresolved() -> None:
    record = UrlRecord("https://example.test", "https://example.test/")

    resolution = PluginResolution(record=record)

    assert resolution.is_resolved is False
    assert resolution.plugin is None
    assert resolution.classification is None


def test_plugin_resolution_is_resolved_when_a_plugin_is_present() -> None:
    record = UrlRecord("https://example.test", "https://example.test/")
    info = PluginInfo(name="example", version="1.0.0", module="tests")

    resolution = PluginResolution(record=record, plugin=info)

    assert resolution.is_resolved is True
