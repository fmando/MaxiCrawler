"""Tests for the provider registry."""

import pytest
from doubles import NotAProvider, StubProvider, make_classification

from maxicrawler.domain import ProviderCapability
from maxicrawler.providers import (
    DuplicateProviderError,
    InvalidProviderError,
    ProviderRegistry,
    ResourceProvider,
    UnknownProviderError,
)


def test_registry_starts_empty() -> None:
    registry = ProviderRegistry()

    assert len(registry) == 0
    assert registry.discover() == ()


def test_registry_registers_and_reports_metadata() -> None:
    registry = ProviderRegistry()

    info = registry.register(StubProvider("mega"))

    assert info.name == "mega"
    assert "mega" in registry
    assert registry.metadata("mega").description == "stub provider mega"


def test_registry_accepts_providers_at_construction() -> None:
    registry = ProviderRegistry([StubProvider("mega"), StubProvider("pixeldrain")])

    assert len(registry) == 2
    assert {info.name for info in registry.discover()} == {"mega", "pixeldrain"}


def test_registry_rejects_a_duplicate_name() -> None:
    registry = ProviderRegistry([StubProvider("mega")])

    with pytest.raises(DuplicateProviderError, match="already registered"):
        registry.register(StubProvider("mega"))


def test_registry_rejects_an_object_that_is_not_a_provider() -> None:
    registry = ProviderRegistry()

    with pytest.raises(InvalidProviderError, match="does not implement"):
        registry.register(NotAProvider())  # type: ignore[arg-type]


def test_registry_unregisters_a_provider() -> None:
    registry = ProviderRegistry([StubProvider("mega")])

    info = registry.unregister("mega")

    assert info.name == "mega"
    assert "mega" not in registry
    assert len(registry) == 0


def test_registry_rejects_unregistering_an_unknown_provider() -> None:
    with pytest.raises(UnknownProviderError, match="not registered"):
        ProviderRegistry().unregister("missing")


def test_registry_rejects_getting_an_unknown_provider() -> None:
    with pytest.raises(UnknownProviderError, match="not registered"):
        ProviderRegistry().get("missing")


def test_registry_orders_providers_by_descending_priority() -> None:
    registry = ProviderRegistry(
        [StubProvider("low", priority=1), StubProvider("high", priority=100)]
    )

    assert [info.name for info in registry.discover()] == ["high", "low"]


def test_registry_keeps_registration_order_for_equal_priorities() -> None:
    registry = ProviderRegistry([StubProvider("first"), StubProvider("second")])

    assert [info.name for info in registry.discover()] == ["first", "second"]


def test_registry_resolves_the_highest_priority_claimant() -> None:
    specific = StubProvider("mega", priority=100, url_prefix="https://mega.nz/")
    fallback = StubProvider("any", priority=0)
    registry = ProviderRegistry([fallback, specific])

    resolved = registry.resolve(make_classification("https://mega.nz/file/AaBbCcDd"))

    assert resolved is specific


def test_registry_returns_none_when_no_provider_claims_the_classification() -> None:
    registry = ProviderRegistry([StubProvider("mega", supports=False)])

    assert registry.resolve(make_classification("https://example.test/")) is None


def test_registry_filters_providers_by_capability() -> None:
    registry = ProviderRegistry(
        [
            StubProvider("inspector"),
            StubProvider(
                "lister",
                capabilities=frozenset({ProviderCapability.INSPECT, ProviderCapability.LIST}),
            ),
        ]
    )

    listing = registry.with_capability(ProviderCapability.LIST)

    assert [info.name for info in listing] == ["lister"]
    assert len(registry.with_capability(ProviderCapability.INSPECT)) == 2
    assert registry.with_capability(ProviderCapability.DOWNLOAD) == ()


def test_registry_iterates_in_resolution_order() -> None:
    registry = ProviderRegistry(
        [StubProvider("low", priority=1), StubProvider("high", priority=100)]
    )

    assert [provider.metadata.name for provider in registry] == ["high", "low"]


def test_stub_provider_satisfies_the_runtime_protocol() -> None:
    assert isinstance(StubProvider("mega"), ResourceProvider)
    assert not isinstance(NotAProvider(), ResourceProvider)
