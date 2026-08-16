"""Persistence adapters."""

from maxicrawler.database.crawls import SQLiteCrawlRepository, StoredCrawl
from maxicrawler.database.discovery import (
    SQLiteDiscoveryRepository,
    StoredSession,
    StoredUrl,
)
from maxicrawler.database.library import IndexedEntry, SQLiteLibraryIndex
from maxicrawler.database.musescore import (
    RequestState,
    ScoreRequest,
    SQLiteRequestQueue,
    StoredRequest,
)
from maxicrawler.database.sqlite import SQLiteDatabase

__all__ = [
    "IndexedEntry",
    "RequestState",
    "SQLiteCrawlRepository",
    "SQLiteDatabase",
    "SQLiteDiscoveryRepository",
    "SQLiteLibraryIndex",
    "SQLiteRequestQueue",
    "ScoreRequest",
    "StoredCrawl",
    "StoredRequest",
    "StoredSession",
    "StoredUrl",
]
