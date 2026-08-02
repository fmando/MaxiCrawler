"""Public package interface for MaxiCrawler."""

from maxicrawler.config import Settings
from maxicrawler.crawler import Crawler, CrawlResult

__all__ = ["CrawlResult", "Crawler", "Settings", "__version__"]

__version__ = "0.1.0"
