"""Composition helper assembling the built-in provider set.

Keeping this wiring in its own module lets
:mod:`maxicrawler.providers.registry` stay unaware of any concrete provider.
"""

from maxicrawler.providers.crypto import CipherBackend, default_cipher_backend
from maxicrawler.providers.mega import MEGA_API_URL, MegaApiClient, MegaProvider
from maxicrawler.providers.mega.provider import DEFAULT_MAX_ENTRIES
from maxicrawler.providers.registry import ProviderRegistry
from maxicrawler.providers.retry import Retrier, RetryPolicy
from maxicrawler.providers.transport import HttpTransport


def create_default_provider_registry(
    *,
    transport: HttpTransport,
    cipher: CipherBackend | None = None,
    retry: RetryPolicy | None = None,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    mega_api_url: str = MEGA_API_URL,
) -> ProviderRegistry:
    """Return a registry holding MaxiCrawler's built-in providers.

    *cipher* defaults to the optional AES backend when it is installed. Passing
    one explicitly keeps the choice in the caller's hands, which is what tests
    and future headless callers want.
    """
    api = MegaApiClient(transport, base_url=mega_api_url, retrier=Retrier(retry))
    mega = MegaProvider(
        api,
        cipher=cipher if cipher is not None else default_cipher_backend(),
        max_entries=max_entries,
    )
    return ProviderRegistry([mega])
