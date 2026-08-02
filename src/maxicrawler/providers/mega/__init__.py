"""The Mega provider: metadata inspection and transfers for public shares."""

from maxicrawler.providers.mega.api import (
    MEGA_API_URL,
    MegaApiClient,
    MegaApiError,
    transfer_url,
)
from maxicrawler.providers.mega.download import counter_block, decrypt_content
from maxicrawler.providers.mega.provider import (
    DEFAULT_MAX_ENTRIES,
    MEGA_PROVIDER_NAME,
    MEGA_PROVIDER_PRIORITY,
    MegaProvider,
    share_url,
)

__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "MEGA_API_URL",
    "MEGA_PROVIDER_NAME",
    "MEGA_PROVIDER_PRIORITY",
    "MegaApiClient",
    "MegaApiError",
    "MegaProvider",
    "counter_block",
    "decrypt_content",
    "share_url",
    "transfer_url",
]
