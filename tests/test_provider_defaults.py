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
    DIRECT_PROVIDER_NAME,
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


def test_the_default_registry_hands_an_ordinary_link_to_the_direct_provider() -> None:
    """Nothing used to claim these, which is why an image had no download button."""
    registry = create_default_provider_registry(transport=RecordingTransport())

    provider = registry.resolve(mega_classification("https://example.test/photo.jpg"))

    assert provider is not None
    assert provider.metadata.name == DIRECT_PROVIDER_NAME


def test_a_mega_link_still_reaches_mega_though_the_direct_provider_claims_it_too() -> None:
    """Priority is the whole of the arrangement.

    The direct provider would faithfully store a Mega link's ciphertext, which
    is the wrong kind of success, so it sits below everything that knows more.
    """
    registry = create_default_provider_registry(transport=RecordingTransport())

    provider = registry.resolve(mega_classification(file_url()))

    assert provider is not None
    assert provider.metadata.name == MEGA_PROVIDER_NAME


def test_the_default_registry_declines_what_is_not_an_http_url() -> None:
    registry = create_default_provider_registry(transport=RecordingTransport())

    assert registry.resolve(mega_classification("mailto:someone@example.test")) is None


def test_the_direct_provider_cannot_transfer_without_being_given_a_transport() -> None:
    """The same switch every provider has, and the visible answer to "does this
    installation fetch arbitrary files?"."""
    registry = create_default_provider_registry(transport=RecordingTransport())

    provider = registry.get(DIRECT_PROVIDER_NAME)

    assert ProviderCapability.DOWNLOAD not in provider.metadata.capabilities


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
