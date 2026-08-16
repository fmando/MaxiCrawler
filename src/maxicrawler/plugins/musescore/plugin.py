"""The MuseScore plugin."""

from maxicrawler import __version__
from maxicrawler.domain import (
    LinkAttribute,
    PluginCapability,
    PluginInfo,
    UrlCategory,
    UrlClassification,
    UrlRecord,
)
from maxicrawler.plugins.musescore.parser import parse_score_url

MUSESCORE_PLUGIN_NAME = "musescore"
"""Registry name of the MuseScore plugin."""

MUSESCORE_PLUGIN_PRIORITY = 100
"""Default priority; above the generic fallback so score pages reach this plugin."""


class MuseScorePlugin:
    """Recognizes MuseScore score pages.

    A score page is classified as a **container**, which is the one decision
    here worth explaining. The page is not a file: it is one piece of music
    offered in several formats, and the two worth keeping are a PDF to read
    from and an MSCZ to edit. Saying *container* is what lets the existing
    planner turn one address into the several jobs it really is, without any
    part of the download chain learning that MuseScore exists.

    It reads the URL string only. Everything else on the domain — a profile, a
    search, the pricing page — is declined, so the generic plugin keeps
    handling it.
    """

    def __init__(self, *, priority: int = MUSESCORE_PLUGIN_PRIORITY) -> None:
        self._metadata = PluginInfo(
            name=MUSESCORE_PLUGIN_NAME,
            version=__version__,
            module=__name__,
            description="Recognizes MuseScore score pages.",
            priority=priority,
            capabilities=frozenset({PluginCapability.CLASSIFY}),
        )

    @property
    def metadata(self) -> PluginInfo:
        """Return the immutable descriptor for this plugin."""
        return self._metadata

    def can_handle(self, record: UrlRecord) -> bool:
        """Return whether *record* addresses a MuseScore score."""
        return parse_score_url(record.raw_url) is not None

    def classify(self, record: UrlRecord) -> UrlClassification:
        """Classify *record*, attaching the score number found in the URL."""
        link = parse_score_url(record.raw_url)
        if link is None:
            return UrlClassification(
                record=record,
                category=UrlCategory.UNSUPPORTED,
                plugin_name=MUSESCORE_PLUGIN_NAME,
            )
        return UrlClassification(
            record=record,
            category=UrlCategory.CONTAINER,
            plugin_name=MUSESCORE_PLUGIN_NAME,
            attributes=(
                LinkAttribute("score_id", link.score_id),
                LinkAttribute("score_url", link.url),
            ),
        )
