"""Dependency-light shared utilities."""

from maxicrawler.utils.logging import configure_logging
from maxicrawler.utils.urls import DuplicateDetector, normalize_url

__all__ = ["DuplicateDetector", "configure_logging", "normalize_url"]
