"""Tests for the built-in provider composition."""

from mega_fixtures import (
    RecordingTransport,
    StubStreamTransport,
    file_answer,
    file_url,
    mega_classification,
)

from maxicrawler.domain import ProviderCapability
from maxicrawler.providers import (
    CryptographyCipherBackend,
    ProviderRegistry,
    RetryPolicy,
    create_default_provider_registry,
)
from maxicrawler.providers.mega import MEGA_PROVIDER_NAME


def test_the_default_registry_holds_the_mega_provider() -> None:
    registry = create_default_provider_registry(transport=RecordingTransport())

    assert isinstance(registry, ProviderRegistry)
    assert MEGA_PROVIDER_NAME in registry
    assert registry.with_capability(ProviderCapability.INSPECT)


def test_the_default_registry_resolves_a_mega_link() -> None:
    registry = create_default_provider_registry(transport=RecordingTransport())

    provider = registry.resolve(mega_classification(file_url()))

    assert provider is not None
    assert provider.metadata.name == MEGA_PROVIDER_NAME


def test_the_default_registry_declines_an_unrelated_link() -> None:
    registry = create_default_provider_registry(transport=RecordingTransport())

    assert registry.resolve(mega_classification("https://example.test/file")) is None


def test_the_default_registry_wires_the_transport_through() -> None:
    transport = RecordingTransport([[file_answer()]])
    registry = create_default_provider_registry(transport=transport)
    provider = registry.get(MEGA_PROVIDER_NAME)

    provider.inspect(provider.reference(mega_classification(file_url())))

    assert transport.calls[0].command["a"] == "g"


def test_the_default_registry_accepts_an_explicit_cipher_and_schedule() -> None:
    registry = create_default_provider_registry(
        transport=RecordingTransport(),
        cipher=CryptographyCipherBackend(),
        retry=RetryPolicy(max_attempts=1),
        max_entries=5,
        mega_api_url="https://eu.api.mega.co.nz/cs",
    )

    assert MEGA_PROVIDER_NAME in registry


def test_a_registry_without_a_stream_cannot_download() -> None:
    registry = create_default_provider_registry(transport=RecordingTransport())

    assert registry.with_capability(ProviderCapability.DOWNLOAD) == ()


def test_a_registry_with_a_stream_can_download() -> None:
    registry = create_default_provider_registry(
        transport=RecordingTransport(),
        stream=StubStreamTransport(),
        cipher=CryptographyCipherBackend(),
    )

    assert [info.name for info in registry.with_capability(ProviderCapability.DOWNLOAD)] == [
        MEGA_PROVIDER_NAME
    ]
