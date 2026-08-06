"""Dependency-light shared utilities."""

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
    "DuplicateDetector",
    "configure_logging",
    "normalize_url",
    "require_http_scheme",
    "safe_target",
    "strip_fragment",
]
