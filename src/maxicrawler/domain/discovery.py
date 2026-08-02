"""Immutable results produced by the discovery pipeline."""

from dataclasses import dataclass

from maxicrawler.domain.models import UrlRecord
from maxicrawler.domain.plugins import PluginResolution


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """The result of handling one URL candidate.

    ``resolution`` is ``None`` for duplicates, which are not resolved again.
    """

    record: UrlRecord
    is_duplicate: bool
    resolution: PluginResolution | None = None
