"""The Mega provider plugin."""

from maxicrawler import __version__
from maxicrawler.domain import (
    LinkAttribute,
    PluginCapability,
    PluginInfo,
    UrlCategory,
    UrlClassification,
    UrlRecord,
)
from maxicrawler.plugins.mega.models import MegaLink, MegaLinkKind
from maxicrawler.plugins.mega.parser import parse_mega_url

MEGA_PLUGIN_NAME = "mega"
"""Registry name of the Mega plugin."""

MEGA_PLUGIN_PRIORITY = 100
"""Default priority; above the generic fallback so Mega links reach this plugin."""

_CATEGORIES = {
    MegaLinkKind.FILE: UrlCategory.FILE,
    MegaLinkKind.FOLDER: UrlCategory.CONTAINER,
}


class MegaPlugin:
    """Recognizes Mega share links and reports what they contain.

    The plugin distinguishes file shares from folder shares and extracts the
    public handle, the decryption key when the URL carries one, and any single
    entry selected inside a folder. Both the modern ``/file/<handle>#<key>``
    form and the legacy ``#!<handle>!<key>`` form are understood, on
    ``mega.nz`` as well as the historical ``mega.co.nz``.

    It reads the URL string only: no network request, no API call, and no
    attempt to use the key. URLs on a Mega host that are not share links —
    the pricing page, for instance — are declined, so the generic fallback
    keeps handling them.
    """

    def __init__(self, *, priority: int = MEGA_PLUGIN_PRIORITY) -> None:
        self._metadata = PluginInfo(
            name=MEGA_PLUGIN_NAME,
            version=__version__,
            module=__name__,
            description="Recognizes Mega file and folder share links.",
            priority=priority,
            capabilities=frozenset({PluginCapability.CLASSIFY}),
        )

    @property
    def metadata(self) -> PluginInfo:
        """Return the immutable descriptor for this plugin."""
        return self._metadata

    def can_handle(self, record: UrlRecord) -> bool:
        """Return whether *record* is a recognizable Mega share link."""
        return parse_mega_url(record.raw_url) is not None

    def classify(self, record: UrlRecord) -> UrlClassification:
        """Classify *record*, attaching the identifiers found in the URL.

        The original URL is parsed rather than the normalized one, so that a
        case-sensitive decryption key survives verbatim.
        """
        link = parse_mega_url(record.raw_url)
        if link is None:
            return UrlClassification(
                record=record,
                category=UrlCategory.UNSUPPORTED,
                plugin_name=MEGA_PLUGIN_NAME,
            )
        return UrlClassification(
            record=record,
            category=_CATEGORIES[link.kind],
            plugin_name=MEGA_PLUGIN_NAME,
            attributes=_attributes(link),
        )


def _attributes(link: MegaLink) -> tuple[LinkAttribute, ...]:
    """Return the structured metadata describing *link*."""
    attributes = [
        LinkAttribute("kind", str(link.kind)),
        LinkAttribute("format", str(link.link_format)),
        LinkAttribute("handle", link.handle),
    ]
    if link.key is not None:
        attributes.append(LinkAttribute("key", link.key))
    if link.node_handle is not None:
        attributes.append(LinkAttribute("node_handle", link.node_handle))
    if link.node_kind is not None:
        attributes.append(LinkAttribute("node_kind", str(link.node_kind)))
    return tuple(attributes)
