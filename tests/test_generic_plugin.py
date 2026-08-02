"""Tests for the built-in generic plugin."""

import socket
from typing import Any, NoReturn

import pytest
from doubles import make_record

from maxicrawler import __version__
from maxicrawler.domain import PluginCapability, UrlCategory
from maxicrawler.plugins import (
    GENERIC_PLUGIN_PRIORITY,
    CrawlerPlugin,
    GenericPlugin,
    create_default_registry,
)


def test_generic_plugin_implements_the_crawler_plugin_protocol() -> None:
    assert isinstance(GenericPlugin(), CrawlerPlugin)


def test_metadata_describes_a_low_priority_classifier() -> None:
    info = GenericPlugin().metadata

    assert info.name == "generic"
    assert info.version == __version__
    assert info.module == "maxicrawler.plugins.generic"
    assert info.priority == GENERIC_PLUGIN_PRIORITY
    assert info.priority < 0
    assert info.supports(PluginCapability.CLASSIFY) is True
    assert info.supports(PluginCapability.DOWNLOAD) is False


def test_metadata_priority_is_configurable() -> None:
    assert GenericPlugin(priority=5).metadata.priority == 5


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/",
        "https://example.test/",
        "https://example.test/docs?a=1",
        "HTTPS://example.test/",
    ],
)
def test_can_handle_accepts_ordinary_http_urls(url: str) -> None:
    assert GenericPlugin().can_handle(make_record(url)) is True


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.test/file",
        "mailto:user@example.test",
        "magnet:?xt=urn:btih:abc",
        "/relative/path",
        "http:///no-host",
        "",
    ],
)
def test_can_handle_rejects_everything_else(url: str) -> None:
    assert GenericPlugin().can_handle(make_record(url)) is False


def test_classify_reports_generic_for_http_urls() -> None:
    record = make_record("https://example.test/docs")

    classification = GenericPlugin().classify(record)

    assert classification.record is record
    assert classification.category is UrlCategory.GENERIC
    assert classification.plugin_name == "generic"
    assert classification.is_supported is True


def test_classify_reports_unsupported_instead_of_raising() -> None:
    classification = GenericPlugin().classify(make_record("ftp://example.test/file"))

    assert classification.category is UrlCategory.UNSUPPORTED
    assert classification.is_supported is False


def test_classification_performs_no_network_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError("the generic plugin must not open sockets")

    monkeypatch.setattr(socket, "socket", fail)
    monkeypatch.setattr(socket, "create_connection", fail)
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    plugin = GenericPlugin()
    record = make_record("https://example.test/docs")

    assert plugin.can_handle(record) is True
    assert plugin.classify(record).category is UrlCategory.GENERIC


def test_default_registry_contains_only_the_generic_plugin() -> None:
    registry = create_default_registry()

    assert [info.name for info in registry.discover()] == ["generic"]
