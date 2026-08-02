"""Immutable core domain models for MaxiCrawler."""

from maxicrawler.domain.discovery import DiscoveryResult
from maxicrawler.domain.models import (
    DownloadTask,
    ScanSession,
    Statistics,
    UrlRecord,
)
from maxicrawler.domain.plugins import (
    LinkAttribute,
    PluginCapability,
    PluginInfo,
    PluginResolution,
    UrlCategory,
    UrlClassification,
)
from maxicrawler.domain.providers import (
    Availability,
    ProviderCapability,
    ProviderInfo,
    ResourceEntry,
    ResourceInspection,
    ResourceKind,
    ResourceMetadata,
    ResourceRef,
    ResourceSecret,
)

__all__ = [
    "Availability",
    "DiscoveryResult",
    "DownloadTask",
    "LinkAttribute",
    "PluginCapability",
    "PluginInfo",
    "PluginResolution",
    "ProviderCapability",
    "ProviderInfo",
    "ResourceEntry",
    "ResourceInspection",
    "ResourceKind",
    "ResourceMetadata",
    "ResourceRef",
    "ResourceSecret",
    "ScanSession",
    "Statistics",
    "UrlCategory",
    "UrlClassification",
    "UrlRecord",
]
