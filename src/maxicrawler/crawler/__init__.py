"""Discovery primitives shared by the offline and the web workflows."""

from maxicrawler.crawler.discovery import DiscoveryPipeline
from maxicrawler.crawler.local_discovery import LocalDiscoveryService
from maxicrawler.crawler.repository import DiscoveryRepository, NullDiscoveryRepository
from maxicrawler.crawler.summary import DiscoverySummary, PluginUsage, to_plugin_usage

__all__ = [
    "DiscoveryPipeline",
    "DiscoveryRepository",
    "DiscoverySummary",
    "LocalDiscoveryService",
    "NullDiscoveryRepository",
    "PluginUsage",
    "to_plugin_usage",
]
