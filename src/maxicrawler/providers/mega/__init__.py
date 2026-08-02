"""The Mega provider: metadata inspection for public Mega shares."""

from maxicrawler.providers.mega.api import MEGA_API_URL, MegaApiClient, MegaApiError
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
    "share_url",
]
