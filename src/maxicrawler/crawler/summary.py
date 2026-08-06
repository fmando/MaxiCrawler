"""What one discovery session amounted to.

These live apart from any one workflow because more than one produces them: a
run over local documents and a crawl of a web page answer the same question
about different sources, and they report it in the same shape so the numbers
stay comparable and one renderer serves both.
"""

from collections import Counter
from dataclasses import dataclass

from maxicrawler.domain import ScanSession, Statistics


@dataclass(frozen=True, slots=True)
class PluginUsage:
    """How often one plugin was responsible for a discovered URL."""

    name: str
    count: int


@dataclass(frozen=True, slots=True)
class DiscoverySummary:
    """The outcome of one discovery session."""

    session: ScanSession
    statistics: Statistics
    plugin_usage: tuple[PluginUsage, ...]

    @property
    def documents_processed(self) -> int:
        """Return how many source documents were read.

        A fetched web page counts as one, which is what lets a crawl and a
        local run be read side by side.
        """
        return self.statistics.documents_processed

    @property
    def total_urls(self) -> int:
        """Return every URL handed to the pipeline, duplicates included."""
        return self.statistics.discovered_urls + self.statistics.duplicate_urls

    @property
    def unique_urls(self) -> int:
        """Return the URLs that were seen for the first time."""
        return self.statistics.discovered_urls

    @property
    def duplicates_removed(self) -> int:
        """Return the URLs dropped because they had already been seen."""
        return self.statistics.duplicate_urls


def to_plugin_usage(counter: Counter[str]) -> tuple[PluginUsage, ...]:
    """Return usage entries ordered by descending count, then by name."""
    ordered = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return tuple(PluginUsage(name=name, count=count) for name, count in ordered)
