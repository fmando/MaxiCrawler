"""Dependency-light shared utilities."""

from maxicrawler.utils.addresses import PrivateNetworkRule, Resolver
from maxicrawler.utils.formatting import (
    SIZE_UNITS,
    UNKNOWN_SIZE,
    elide_middle,
    format_size,
    parse_size,
)
from maxicrawler.utils.logging import configure_logging
from maxicrawler.utils.urls import (
    HTTP_SCHEMES,
    DuplicateDetector,
    normalize_url,
    require_http_scheme,
    safe_target,
    strip_fragment,
)

__all__ = [
    "HTTP_SCHEMES",
    "SIZE_UNITS",
    "UNKNOWN_SIZE",
    "DuplicateDetector",
    "PrivateNetworkRule",
    "Resolver",
    "configure_logging",
    "elide_middle",
    "format_size",
    "normalize_url",
    "parse_size",
    "require_http_scheme",
    "safe_target",
    "strip_fragment",
]
