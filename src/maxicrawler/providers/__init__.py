"""The provider layer: what can be done with a classified resource.

The plugin layer in :mod:`maxicrawler.plugins` answers *"can I classify this
URL?"* using nothing but the URL string. This layer answers the follow-up
question, *"what can I do with this resource?"*, and is the only place in
MaxiCrawler that reaches a remote host.
"""

from maxicrawler.providers.crypto import (
    BlockStream,
    CipherBackend,
    CryptographyCipherBackend,
    default_cipher_backend,
)
from maxicrawler.providers.defaults import create_default_provider_registry
from maxicrawler.providers.direct import (
    DIRECT_PROVIDER_NAME,
    DIRECT_PROVIDER_PRIORITY,
    DirectProvider,
)
from maxicrawler.providers.errors import (
    AddressRefusedError,
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
from maxicrawler.providers.mega import (
    MEGA_API_URL,
    MEGA_PROVIDER_NAME,
    MEGA_PROVIDER_PRIORITY,
    MegaApiClient,
    MegaApiError,
    MegaProvider,
)
from maxicrawler.providers.protocol import DownloadSink, ResourceProvider
from maxicrawler.providers.registry import ProviderRegistry
from maxicrawler.providers.retry import Retrier, RetryPolicy
from maxicrawler.providers.transport import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_MAX_REDIRECTS,
    DEFAULT_TIMEOUT,
    FileTransport,
    HttpTransport,
    RemoteFile,
    StreamTransport,
    UrllibFileTransport,
    UrllibStreamTransport,
    UrllibTransport,
)

__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_MAX_REDIRECTS",
    "DEFAULT_TIMEOUT",
    "DIRECT_PROVIDER_NAME",
    "DIRECT_PROVIDER_PRIORITY",
    "MEGA_API_URL",
    "MEGA_PROVIDER_NAME",
    "MEGA_PROVIDER_PRIORITY",
    "AddressRefusedError",
    "BlockStream",
    "CipherBackend",
    "CryptographyCipherBackend",
    "DirectProvider",
    "DownloadSink",
    "DuplicateProviderError",
    "FileTransport",
    "HttpTransport",
    "InvalidProviderError",
    "MegaApiClient",
    "MegaApiError",
    "MegaProvider",
    "ProviderCryptoError",
    "ProviderDependencyError",
    "ProviderError",
    "ProviderProtocolError",
    "ProviderRateLimitError",
    "ProviderRegistry",
    "ProviderRegistryError",
    "ProviderTransportError",
    "RemoteFile",
    "ResourceProvider",
    "Retrier",
    "RetryPolicy",
    "StreamTransport",
    "UnknownProviderError",
    "UnsupportedResourceError",
    "UrllibFileTransport",
    "UrllibStreamTransport",
    "UrllibTransport",
    "create_default_provider_registry",
    "default_cipher_backend",
]
