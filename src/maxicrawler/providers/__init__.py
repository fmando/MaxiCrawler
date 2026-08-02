"""The provider layer: what can be done with a classified resource.

The plugin layer in :mod:`maxicrawler.plugins` answers *"can I classify this
URL?"* using nothing but the URL string. This layer answers the follow-up
question, *"what can I do with this resource?"*, and is the only place in
MaxiCrawler that reaches a remote host.
"""

from maxicrawler.providers.errors import (
    DuplicateProviderError,
    InvalidProviderError,
    ProviderCryptoError,
    ProviderDependencyError,
    ProviderError,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderRegistryError,
    ProviderTransportError,
    UnknownProviderError,
    UnsupportedResourceError,
)
from maxicrawler.providers.protocol import ResourceProvider
from maxicrawler.providers.registry import ProviderRegistry

__all__ = [
    "DuplicateProviderError",
    "InvalidProviderError",
    "ProviderCryptoError",
    "ProviderDependencyError",
    "ProviderError",
    "ProviderProtocolError",
    "ProviderRateLimitError",
    "ProviderRegistry",
    "ProviderRegistryError",
    "ProviderTransportError",
    "ResourceProvider",
    "UnknownProviderError",
    "UnsupportedResourceError",
]
