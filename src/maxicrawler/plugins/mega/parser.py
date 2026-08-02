"""Recognition of Mega share URLs.

Parsing is a pure string operation: no network request, no API call, and no
attempt to decrypt anything. The parser only reports what the URL states.
"""

import re
from urllib.parse import urlsplit

from maxicrawler.plugins.mega.models import MegaLink, MegaLinkFormat, MegaLinkKind

MEGA_HOSTS = frozenset({"mega.nz", "www.mega.nz", "mega.co.nz", "www.mega.co.nz"})
"""Hosts that serve Mega share links; ``mega.co.nz`` is the historical domain."""

HANDLE = r"[A-Za-z0-9_-]{8}"
"""A public node handle: eight base64url characters."""

KEY = r"[A-Za-z0-9_-]{16,}"
"""A decryption key: base64url, 22 characters for folders and 43 for files."""

_MODERN_PATH = re.compile(rf"^/(?P<kind>file|folder)/(?P<handle>{HANDLE})/?$")
_MODERN_FRAGMENT = re.compile(
    rf"^(?P<key>{KEY})(?:/(?P<node_kind>file|folder)/(?P<node_handle>{HANDLE}))?/?$"
)
_LEGACY_FRAGMENT = re.compile(
    rf"^(?P<folder>F?)!(?P<handle>{HANDLE})(?:!(?P<key>{KEY}))?(?:!(?P<node_handle>{HANDLE}))?$"
)


def parse_mega_url(url: str) -> MegaLink | None:
    """Return the Mega link *url* describes, or ``None`` if it is not one.

    Recognition is strict about identity and lenient about the key. A modern
    link is identified by its path, so an unreadable fragment yields a link
    without a key rather than a rejection. A legacy link keeps its identity in
    the fragment, so an unreadable fragment means the URL is not recognized.
    """
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    hostname = parsed.hostname
    if hostname is None or hostname.lower() not in MEGA_HOSTS:
        return None
    modern = _parse_modern(parsed.path, parsed.fragment)
    return modern if modern is not None else _parse_legacy(parsed.path, parsed.fragment)


def _parse_modern(path: str, fragment: str) -> MegaLink | None:
    """Parse ``/file/<handle>#<key>`` and ``/folder/<handle>#<key>/file/<node>``."""
    path_match = _MODERN_PATH.match(path)
    if path_match is None:
        return None
    key: str | None = None
    node_handle: str | None = None
    node_kind: MegaLinkKind | None = None
    fragment_match = _MODERN_FRAGMENT.match(fragment) if fragment else None
    if fragment_match is not None:
        key = fragment_match["key"]
        node_handle = fragment_match["node_handle"]
        if node_handle is not None:
            node_kind = MegaLinkKind(fragment_match["node_kind"])
    return MegaLink(
        kind=MegaLinkKind(path_match["kind"]),
        link_format=MegaLinkFormat.MODERN,
        handle=path_match["handle"],
        key=key,
        node_handle=node_handle,
        node_kind=node_kind,
    )


def _parse_legacy(path: str, fragment: str) -> MegaLink | None:
    """Parse ``#!<handle>!<key>`` and ``#F!<handle>!<key>!<node>``."""
    if path not in {"", "/"}:
        return None
    match = _LEGACY_FRAGMENT.match(fragment)
    if match is None:
        return None
    return MegaLink(
        kind=MegaLinkKind.FOLDER if match["folder"] else MegaLinkKind.FILE,
        link_format=MegaLinkFormat.LEGACY,
        handle=match["handle"],
        key=match["key"],
        node_handle=match["node_handle"],
    )
