"""Typed, immutable models shared across application boundaries."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class UrlRecord:
    """A discovered URL together with its canonical representation."""

    raw_url: str
    normalized_url: str
    source_url: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """The result of handling one URL candidate."""

    record: UrlRecord
    is_duplicate: bool


@dataclass(frozen=True, slots=True)
class DownloadTask:
    """A future download request; this sprint does not execute it."""

    record: UrlRecord
    priority: int = 0


@dataclass(frozen=True, slots=True)
class PluginInfo:
    """Describes a loaded extension without coupling to its implementation."""

    name: str
    version: str
    module: str


@dataclass(frozen=True, slots=True)
class ScanSession:
    """Identifies one discovery session."""

    session_id: str
    started_at: datetime


@dataclass(frozen=True, slots=True)
class Statistics:
    """Immutable counters collected during a discovery session."""

    discovered_urls: int = 0
    duplicate_urls: int = 0
    queued_downloads: int = 0
    completed_downloads: int = 0
    failed_downloads: int = 0

    def with_discovery(self, *, duplicate: bool) -> "Statistics":
        """Return counters updated for a processed URL candidate."""
        if duplicate:
            return Statistics(
                discovered_urls=self.discovered_urls,
                duplicate_urls=self.duplicate_urls + 1,
                queued_downloads=self.queued_downloads,
                completed_downloads=self.completed_downloads,
                failed_downloads=self.failed_downloads,
            )
        return Statistics(
            discovered_urls=self.discovered_urls + 1,
            duplicate_urls=self.duplicate_urls,
            queued_downloads=self.queued_downloads,
            completed_downloads=self.completed_downloads,
            failed_downloads=self.failed_downloads,
        )
