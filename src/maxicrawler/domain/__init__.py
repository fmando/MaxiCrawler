"""Immutable core domain models for MaxiCrawler."""

from maxicrawler.domain.discovery import DiscoveryResult
from maxicrawler.domain.models import (
    DownloadTask,
    ScanSession,
    Statistics,
    UrlRecord,
)
from maxicrawler.domain.plugins import (
    PluginCapability,
    PluginInfo,
    PluginResolution,
    UrlCategory,
    UrlClassification,
)

__all__ = [
    "DiscoveryResult",
    "DownloadTask",
    "PluginCapability",
    "PluginInfo",
    "PluginResolution",
    "ScanSession",
    "Statistics",
    "UrlCategory",
    "UrlClassification",
    "UrlRecord",
]
