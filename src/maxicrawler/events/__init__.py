"""Synchronous events and event delivery."""

from maxicrawler.events.bus import EventBus
from maxicrawler.events.types import (
    CrawlFinished,
    CrawlStarted,
    DownloadFailed,
    DownloadFinished,
    DownloadQueued,
    PageCrawled,
    PageFailed,
    PluginLoaded,
    PluginUnloaded,
    ScanFinished,
    ScanStarted,
    UrlDiscovered,
)

__all__ = [
    "CrawlFinished",
    "CrawlStarted",
    "DownloadFailed",
    "DownloadFinished",
    "DownloadQueued",
    "EventBus",
    "PageCrawled",
    "PageFailed",
    "PluginLoaded",
    "PluginUnloaded",
    "ScanFinished",
    "ScanStarted",
    "UrlDiscovered",
]
