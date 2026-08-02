"""Download protocols."""

from typing import Protocol


class Downloader(Protocol):
    """Fetches a URL and returns its decoded document body."""

    def fetch(self, url: str) -> str: ...
