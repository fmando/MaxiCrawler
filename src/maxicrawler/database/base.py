"""Persistence protocols."""

from typing import Protocol


class CrawlStore(Protocol):
    """Stores a fetched document by canonical URL."""

    def save(self, url: str, document: str) -> None: ...
