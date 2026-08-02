"""Registration, discovery, and lookup of MaxiCrawler providers."""

from collections.abc import Iterable, Iterator

from maxicrawler.domain import ProviderCapability, ProviderInfo, UrlClassification
from maxicrawler.providers.errors import (
    DuplicateProviderError,
    InvalidProviderError,
    UnknownProviderError,
)
from maxicrawler.providers.protocol import ResourceProvider


class ProviderRegistry:
    """Owns the active providers and finds the one responsible for a URL.

    Providers are ordered by descending :attr:`ProviderInfo.priority`, and
    providers sharing a priority keep their registration order, so resolution
    is deterministic. The registry mirrors
    :class:`~maxicrawler.plugins.registry.PluginRegistry` on purpose: the two
    layers are looked up the same way even though they answer different
    questions.

    The registry itself performs no I/O; it only decides who to ask.
    """

    def __init__(self, providers: Iterable[ResourceProvider] = ()) -> None:
        self._providers: dict[str, ResourceProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: ResourceProvider) -> ProviderInfo:
        """Add *provider* to the registry and return its metadata.

        Raises:
            InvalidProviderError: *provider* does not implement the protocol.
            DuplicateProviderError: a provider with the same name is registered.
        """
        if not isinstance(provider, ResourceProvider):
            msg = f"object does not implement ResourceProvider: {provider!r}"
            raise InvalidProviderError(msg)
        info = provider.metadata
        if info.name in self._providers:
            msg = f"provider already registered: {info.name}"
            raise DuplicateProviderError(msg)
        self._providers[info.name] = provider
        return info

    def unregister(self, name: str) -> ProviderInfo:
        """Remove the provider called *name* and return its metadata.

        Raises:
            UnknownProviderError: no provider is registered under *name*.
        """
        provider = self._providers.pop(name, None)
        if provider is None:
            msg = f"provider is not registered: {name}"
            raise UnknownProviderError(msg)
        return provider.metadata

    def discover(self) -> tuple[ProviderInfo, ...]:
        """Return the metadata of every provider in resolution order."""
        return tuple(provider.metadata for provider in self._ordered())

    def metadata(self, name: str) -> ProviderInfo:
        """Return the metadata of the provider called *name*.

        Raises:
            UnknownProviderError: no provider is registered under *name*.
        """
        return self.get(name).metadata

    def get(self, name: str) -> ResourceProvider:
        """Return the provider called *name*.

        Raises:
            UnknownProviderError: no provider is registered under *name*.
        """
        provider = self._providers.get(name)
        if provider is None:
            msg = f"provider is not registered: {name}"
            raise UnknownProviderError(msg)
        return provider

    def resolve(self, classification: UrlClassification) -> ResourceProvider | None:
        """Return the highest-priority provider claiming *classification*, if any."""
        for provider in self._ordered():
            if provider.supports(classification):
                return provider
        return None

    def with_capability(self, capability: ProviderCapability) -> tuple[ProviderInfo, ...]:
        """Return the metadata of providers advertising *capability*."""
        return tuple(info for info in self.discover() if info.supports(capability))

    def _ordered(self) -> tuple[ResourceProvider, ...]:
        """Return the providers sorted by descending priority, ties kept stable."""
        return tuple(sorted(self._providers.values(), key=lambda p: -p.metadata.priority))

    def __contains__(self, name: object) -> bool:
        """Return whether a provider is registered under *name*."""
        return name in self._providers

    def __iter__(self) -> Iterator[ResourceProvider]:
        """Iterate over the registered providers in resolution order."""
        return iter(self._ordered())

    def __len__(self) -> int:
        """Return the number of registered providers."""
        return len(self._providers)
