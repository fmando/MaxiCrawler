"""Persistence adapters."""

from maxicrawler.database.crawls import SQLiteCrawlRepository, StoredCrawl
from maxicrawler.database.discovery import (
    SQLiteDiscoveryRepository,
    StoredSession,
    StoredUrl,
)
from maxicrawler.database.sqlite import SQLiteDatabase

__all__ = [
    "SQLiteCrawlRepository",
    "SQLiteDatabase",
    "SQLiteDiscoveryRepository",
    "StoredCrawl",
    "StoredSession",
    "StoredUrl",
]
