"""Domain event value objects.

The crawl events carry plain values rather than the crawl models themselves.
That is not laziness: ``maxicrawler.web`` already depends on this package
through the discovery pipeline, so an event holding a
:class:`~maxicrawler.web.session.CrawlSession` would close a cycle. Plain
values are also the right shape for something a future user interface consumes
over a socket.
"""

from dataclasses import dataclass

from maxicrawler.domain import DownloadTask, PluginInfo, ScanSession, Statistics, UrlRecord


@dataclass(frozen=True, slots=True)
class UrlDiscovered:
    record: UrlRecord


@dataclass(frozen=True, slots=True)
class ScanStarted:
    session: ScanSession


@dataclass(frozen=True, slots=True)
class ScanFinished:
    session: ScanSession
    statistics: Statistics


@dataclass(frozen=True, slots=True)
class PluginLoaded:
    plugin: PluginInfo


@dataclass(frozen=True, slots=True)
class PluginUnloaded:
    plugin: PluginInfo


@dataclass(frozen=True, slots=True)
class DownloadQueued:
    task: DownloadTask


@dataclass(frozen=True, slots=True)
class DownloadFinished:
    task: DownloadTask


@dataclass(frozen=True, slots=True)
class DownloadFailed:
    task: DownloadTask
    reason: str


@dataclass(frozen=True, slots=True)
class CrawlStarted:
    """A recursive crawl began at *seed_url*."""

    session_id: str
    seed_url: str
    max_depth: int


@dataclass(frozen=True, slots=True)
class PageCrawled:
    """One page was fetched and read."""

    session_id: str
    url: str
    final_url: str
    depth: int
    status: int
    link_count: int


@dataclass(frozen=True, slots=True)
class PageFailed:
    """One page could not be read; the crawl carried on."""

    session_id: str
    url: str
    depth: int
    reason: str


@dataclass(frozen=True, slots=True)
class CrawlFinished:
    """A crawl reached a terminal state."""

    session_id: str
    state: str
    pages_visited: int
    pages_failed: int


Event = (
    UrlDiscovered
    | ScanStarted
    | ScanFinished
    | PluginLoaded
    | PluginUnloaded
    | DownloadQueued
    | DownloadFinished
    | DownloadFailed
    | CrawlStarted
    | PageCrawled
    | PageFailed
    | CrawlFinished
)
