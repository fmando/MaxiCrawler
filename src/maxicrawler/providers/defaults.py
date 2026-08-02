"""Composition helper assembling the built-in provider set.

Keeping this wiring in its own module lets
:mod:`maxicrawler.providers.registry` stay unaware of any concrete provider.
"""

from maxicrawler.providers.crypto import CipherBackend, default_cipher_backend
from maxicrawler.providers.mega import MEGA_API_URL, MegaApiClient, MegaProvider
from maxicrawler.providers.mega.provider import DEFAULT_MAX_ENTRIES
from maxicrawler.providers.registry import ProviderRegistry
from maxicrawler.providers.retry import Retrier, RetryPolicy
from maxicrawler.providers.transport import DEFAULT_CHUNK_SIZE, HttpTransport, StreamTransport


def create_default_provider_registry(
    *,
    transport: HttpTransport,
    stream: StreamTransport | None = None,
    cipher: CipherBackend | None = None,
    retry: RetryPolicy | None = None,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    mega_api_url: str = MEGA_API_URL,
) -> ProviderRegistry:
    """Return a registry holding MaxiCrawler's built-in providers.

    *cipher* defaults to the optional AES backend when it is installed. Passing
    one explicitly keeps the choice in the caller's hands, which is what tests
    and future headless callers want.

    *stream* is what makes downloading possible. Leaving it out yields
    inspection-only providers, which is exactly what a command that must not
    move any content wants — and the providers say so through their
    capabilities rather than by failing when asked.
    """
    api = MegaApiClient(transport, base_url=mega_api_url, retrier=Retrier(retry))
    mega = MegaProvider(
        api,
        cipher=cipher if cipher is not None else default_cipher_backend(),
        stream=stream,
        max_entries=max_entries,
        chunk_size=chunk_size,
    )
    return ProviderRegistry([mega])
