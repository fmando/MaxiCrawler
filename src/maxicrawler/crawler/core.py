"""Core crawl coordination primitives."""

from collections.abc import Iterable
from dataclasses import dataclass

from maxicrawler.config import Settings


@dataclass(frozen=True, slots=True)
class CrawlResult:
    """The normalized outcome of one crawl run."""

    visited_urls: tuple[str, ...]


class Crawler:
    """A minimal crawl coordinator ready for downloader integration."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def plan(self, urls: Iterable[str]) -> CrawlResult:
        """Normalize and cap URLs before requesting any network resource."""
        unique_urls = tuple(dict.fromkeys(url.strip() for url in urls if url.strip()))
        return CrawlResult(visited_urls=unique_urls[: self.settings.max_pages])
