"""Typed, immutable models shared across application boundaries."""

from dataclasses import dataclass, replace
from datetime import datetime


@dataclass(frozen=True, slots=True)
class UrlRecord:
    """A discovered URL together with its canonical representation."""

    raw_url: str
    normalized_url: str
    source_url: str | None = None


@dataclass(frozen=True, slots=True)
class DownloadTask:
    """A future download request; this sprint does not execute it."""

    record: UrlRecord
    priority: int = 0


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
    unresolved_urls: int = 0
    queued_downloads: int = 0
    completed_downloads: int = 0
    failed_downloads: int = 0

    def with_discovery(self, *, duplicate: bool, resolved: bool = True) -> "Statistics":
        """Return counters updated for a processed URL candidate.

        *resolved* records whether a plugin claimed the candidate. It is
        ignored for duplicates because duplicates are not resolved again.
        """
        if duplicate:
            return replace(self, duplicate_urls=self.duplicate_urls + 1)
        return replace(
            self,
            discovered_urls=self.discovered_urls + 1,
            unresolved_urls=self.unresolved_urls + (0 if resolved else 1),
        )
