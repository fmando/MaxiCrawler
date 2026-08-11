"""Persistence adapters."""

from maxicrawler.database.crawls import SQLiteCrawlRepository, StoredCrawl
from maxicrawler.database.discovery import (
    SQLiteDiscoveryRepository,
    StoredSession,
    StoredUrl,
)
from maxicrawler.database.library import IndexedEntry, SQLiteLibraryIndex
from maxicrawler.database.sqlite import SQLiteDatabase

__all__ = [
    "IndexedEntry",
    "SQLiteCrawlRepository",
    "SQLiteDatabase",
    "SQLiteDiscoveryRepository",
    "SQLiteLibraryIndex",
    "StoredCrawl",
    "StoredSession",
    "StoredUrl",
]
