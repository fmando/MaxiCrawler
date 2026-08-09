"""Dependency-light shared utilities."""

from maxicrawler.utils.formatting import SIZE_UNITS, UNKNOWN_SIZE, format_size
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
    "configure_logging",
    "format_size",
    "normalize_url",
    "require_http_scheme",
    "safe_target",
    "strip_fragment",
]
