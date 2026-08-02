"""In-memory discovery primitives; network crawling is not implemented."""

from maxicrawler.crawler.discovery import DiscoveryPipeline
from maxicrawler.crawler.repository import DiscoveryRepository, NullDiscoveryRepository

__all__ = ["DiscoveryPipeline", "DiscoveryRepository", "NullDiscoveryRepository"]
