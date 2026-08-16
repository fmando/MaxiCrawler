"""Reading a MuseScore score address, without asking MuseScore anything.

The whole of what a plugin may know about this host: a score has a number, the
number is in the path, and everything else on the domain is somebody else's
business. No request, no session, no guessing at what the page contains.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

MUSESCORE_DOMAIN = "musescore.com"
"""The host, without the language subdomains that also serve it."""

SCORE_PATH = re.compile(r"/scores/(\d+)(?:/|$)")
"""Where a score number sits, in either address shape the site uses.

``/user/21965011/scores/4217351`` is the canonical form and
``/michael_sisley/scores/4217351`` is the same score under a vanity profile.
Matching the tail rather than the whole path means the second costs nothing to
support and a third shape would too. The number is required to be digits, which
is what keeps ``/scores/browse`` from looking like a score.
"""

TRAILING = frozenset({"embed", "piano-tutorial"})
"""Segments that follow a score number and address a *view* of the same score.

An embed URL and a tutorial URL are the same piece of music with a different
player around it. Reducing them to the score is what stops one score from
being downloaded twice under two names.
"""


@dataclass(frozen=True, slots=True)
class ScoreLink:
    """A MuseScore score, identified by number."""

    score_id: str
    """The digits MuseScore calls a score by."""

    url: str
    """The canonical page for the score, with any view segment removed."""


def parse_score_url(raw_url: str) -> ScoreLink | None:
    """Return the score *raw_url* addresses, or ``None`` for anything else.

    Declines everything on the domain that is not a score — the pricing page,
    a user profile, a search — so the generic plugin keeps handling those.
    """
    parsed = urlsplit(raw_url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").lower()
    if host != MUSESCORE_DOMAIN and not host.endswith(f".{MUSESCORE_DOMAIN}"):
        return None
    match = SCORE_PATH.search(parsed.path)
    if match is None or not parsed.path.startswith("/"):
        return None
    remainder = parsed.path[match.end() :].strip("/")
    if remainder and remainder not in TRAILING:
        return None
    score_id = match.group(1)
    canonical = f"{parsed.scheme.lower()}://{host}{parsed.path[: match.end()].rstrip('/')}"
    return ScoreLink(score_id=score_id, url=canonical)
