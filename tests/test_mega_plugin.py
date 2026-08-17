"""Tests for the Mega plugin and its place in the registry."""

import socket
from typing import Any, NoReturn

import pytest
from doubles import StubPlugin

from maxicrawler import __version__
from maxicrawler.domain import PluginCapability, UrlCategory, UrlRecord
from maxicrawler.plugins import (
    GENERIC_PLUGIN_PRIORITY,
    MEGA_PLUGIN_PRIORITY,
    CrawlerPlugin,
    DuplicatePluginError,
    GenericPlugin,
    MegaPlugin,
    PluginRegistry,
    PluginResolver,
    create_default_registry,
)

FILE_KEY = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
FOLDER_KEY = "0123456789abcdefghijkl"
HANDLE = "AaBbCcDd"
NODE = "N0d3H4nd"

FILE_URL = f"https://mega.nz/file/{HANDLE}#{FILE_KEY}"
FOLDER_URL = f"https://mega.nz/folder/{HANDLE}#{FOLDER_KEY}"
LEGACY_FILE_URL = f"https://mega.nz/#!{HANDLE}!{FILE_KEY}"
LEGACY_FOLDER_URL = f"https://mega.nz/#F!{HANDLE}!{FOLDER_KEY}"


def record(url: str) -> UrlRecord:
    """Return a record whose raw URL is *url*, as the extractor would build it."""
    return UrlRecord(raw_url=url, normalized_url=url)


def test_mega_plugin_implements_the_crawler_plugin_protocol() -> None:
    assert isinstance(MegaPlugin(), CrawlerPlugin)


def test_metadata_describes_a_high_priority_classifier() -> None:
    info = MegaPlugin().metadata

    assert info.name == "mega"
    assert info.version == __version__
    assert info.module == "maxicrawler.plugins.mega.plugin"
    assert info.priority == MEGA_PLUGIN_PRIORITY
    assert info.priority > GENERIC_PLUGIN_PRIORITY
    assert info.supports(PluginCapability.CLASSIFY) is True


def test_metadata_priority_is_configurable() -> None:
    assert MegaPlugin(priority=5).metadata.priority == 5


@pytest.mark.parametrize("url", [FILE_URL, FOLDER_URL, LEGACY_FILE_URL, LEGACY_FOLDER_URL])
def test_can_handle_accepts_share_links(url: str) -> None:
    assert MegaPlugin().can_handle(record(url)) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://mega.nz/pro",
        "https://mega.nz/",
        "https://example.test/file/AaBbCcDd",
        "https://mega.nz/file/AaBbCc",
    ],
)
def test_can_handle_declines_everything_else(url: str) -> None:
    assert MegaPlugin().can_handle(record(url)) is False


def test_file_share_is_classified_as_a_file() -> None:
    classification = MegaPlugin().classify(record(FILE_URL))

    assert classification.category is UrlCategory.FILE
    assert classification.plugin_name == "mega"
    assert classification.is_supported is True


def test_folder_share_is_classified_as_a_container() -> None:
    assert MegaPlugin().classify(record(FOLDER_URL)).category is UrlCategory.CONTAINER


def test_classification_exposes_the_public_identifier_and_key() -> None:
    classification = MegaPlugin().classify(record(FILE_URL))

    assert classification.attribute("kind") == "file"
    assert classification.attribute("format") == "modern"
    assert classification.attribute("handle") == HANDLE
    assert classification.attribute("key") == FILE_KEY
    assert classification.attribute("node_handle") is None


def test_classification_of_a_legacy_link_reports_the_legacy_format() -> None:
    classification = MegaPlugin().classify(record(LEGACY_FOLDER_URL))

    assert classification.attribute("format") == "legacy"
    assert classification.attribute("kind") == "folder"
    assert classification.attribute("handle") == HANDLE
    assert classification.attribute("key") == FOLDER_KEY


def test_classification_reports_a_selected_node() -> None:
    url = f"https://mega.nz/folder/{HANDLE}#{FOLDER_KEY}/file/{NODE}"

    classification = MegaPlugin().classify(record(url))

    assert classification.attribute("node_handle") == NODE
    assert classification.attribute("node_kind") == "file"


def test_classification_omits_the_key_when_the_url_has_none() -> None:
    classification = MegaPlugin().classify(record(f"https://mega.nz/file/{HANDLE}"))

    assert classification.attribute("handle") == HANDLE
    assert classification.attribute("key") is None
    assert classification.category is UrlCategory.FILE


def test_classification_reports_unsupported_instead_of_raising() -> None:
    classification = MegaPlugin().classify(record("https://mega.nz/pro"))

    assert classification.category is UrlCategory.UNSUPPORTED
    assert classification.is_supported is False
    assert classification.attributes == ()


def test_classification_uses_the_raw_url_so_keys_survive_normalization() -> None:
    stripped = UrlRecord(raw_url=LEGACY_FILE_URL, normalized_url="https://mega.nz/")

    classification = MegaPlugin().classify(stripped)

    assert classification.attribute("key") == FILE_KEY


def test_classification_performs_no_network_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError("the Mega plugin must not open sockets")

    monkeypatch.setattr(socket, "socket", fail)
    monkeypatch.setattr(socket, "create_connection", fail)
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    plugin = MegaPlugin()

    assert plugin.can_handle(record(FILE_URL)) is True
    assert plugin.classify(record(FILE_URL)).category is UrlCategory.FILE


def test_default_registry_contains_mega_above_generic() -> None:
    """Host plugins in any order, and the generic fallback last.

    Order among the host plugins carries no meaning — each claims its own
    domain and no two overlap. What matters is that ``generic`` stays at the
    bottom, because it claims everything and would otherwise answer first.
    """
    registry = create_default_registry()
    names = [info.name for info in registry.discover()]

    assert set(names) == {"mega", "musescore", "generic"}
    assert names[-1] == "generic"


def test_registry_routes_mega_links_to_the_mega_plugin() -> None:
    resolver = PluginResolver(create_default_registry())

    resolution = resolver.resolve(record(FILE_URL))

    assert resolution.plugin is not None
    assert resolution.plugin.name == "mega"
    assert resolution.classification is not None
    assert resolution.classification.category is UrlCategory.FILE


def test_generic_plugin_still_handles_ordinary_urls() -> None:
    resolver = PluginResolver(create_default_registry())

    resolution = resolver.resolve(record("https://example.test/docs"))

    assert resolution.plugin is not None
    assert resolution.plugin.name == "generic"
    assert resolution.classification is not None
    assert resolution.classification.category is UrlCategory.GENERIC


def test_generic_plugin_handles_non_share_pages_on_the_mega_host() -> None:
    resolver = PluginResolver(create_default_registry())

    resolution = resolver.resolve(record("https://mega.nz/pro"))

    assert resolution.plugin is not None
    assert resolution.plugin.name == "generic"
    assert resolution.classification is not None
    assert resolution.classification.category is UrlCategory.GENERIC


def test_removing_the_mega_plugin_falls_back_to_generic() -> None:
    registry = create_default_registry()
    registry.unregister("mega")
    resolver = PluginResolver(registry)

    resolution = resolver.resolve(record(FILE_URL))

    assert resolution.plugin is not None
    assert resolution.plugin.name == "generic"
    assert resolution.classification is not None
    assert resolution.classification.category is UrlCategory.GENERIC


def test_a_higher_priority_plugin_can_outrank_mega() -> None:
    registry = PluginRegistry([MegaPlugin(), GenericPlugin(), StubPlugin("vip", priority=200)])
    resolver = PluginResolver(registry)

    resolution = resolver.resolve(record(FILE_URL))

    assert [info.name for info in registry.discover()] == ["vip", "mega", "generic"]
    assert resolution.plugin is not None
    assert resolution.plugin.name == "vip"


def test_registering_a_second_plugin_under_the_name_mega_is_rejected() -> None:
    registry = create_default_registry()

    with pytest.raises(DuplicatePluginError):
        registry.register(MegaPlugin(priority=200))
