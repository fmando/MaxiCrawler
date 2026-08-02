"""Persistence adapters."""

from maxicrawler.database.discovery import (
    SQLiteDiscoveryRepository,
    StoredSession,
    StoredUrl,
)
from maxicrawler.database.sqlite import SQLiteDatabase

__all__ = ["SQLiteDatabase", "SQLiteDiscoveryRepository", "StoredSession", "StoredUrl"]
