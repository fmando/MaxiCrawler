"""Reading the state a MuseScore score page carries in its markup.

The page is a React application, so the HTML holds almost nothing a reader
would recognise — but it hands its whole starting state to the browser in one
attribute, and that state is far better than anything scraping could recover.
It names the download URLs outright, complete with the per-score token they
need, and it states the daily allowance and whether it has been spent.

**That is why this exists rather than a scraper.** The allowance can be *asked
for* instead of guessed at, which is what lets the queue keep a limit rather
than discover it by being refused twenty times.

**Why the attribute is found by shape.** Its name is a long hex digest that
changes between deployments, so matching on the name would break on a Tuesday.
What does not change is the shape: one ``data-`` attribute whose value is an
HTML-escaped JSON document with a ``store`` in it.

Nothing here trusts the document. Every field is optional, every lookup is
guarded, and a page that has changed shape produces
:class:`~maxicrawler.providers.musescore.errors.ScorePageError` rather than a
``KeyError`` three layers away.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import unescape
from typing import Any

from maxicrawler.providers.musescore.errors import (
    ChallengeEncounteredError,
    ScorePageError,
    SessionExpiredError,
)

STATE_ATTRIBUTE = re.compile(r'data-[0-9a-f]{32,}="([^"]*)"')
"""One ``data-<digest>`` attribute, captured unparsed.

The value cannot contain a bare double quote: it is an HTML attribute, so every
quote inside it arrives as ``&quot;``. That is what makes a character class
sufficient here and an HTML parser unnecessary.
"""

CHALLENGE_MARKERS = (
    "cf-challenge",
    "/cdn-cgi/challenge-platform/h/",
    "just a moment",
    "enable javascript and cookies to continue",
    "px-captcha",
)
"""Text that means a bot check answered instead of the page.

Recognising one is the whole of MaxiCrawler's involvement with it. Solving a
challenge is a non-goal (VISION.md), so this exists to *stop*, with a sentence
that says what happened, rather than to work around anything.
"""

LOGIN_MARKERS = (
    "/user/login",
    '"isauthenticated":false',
    "hasproaccess&quot;:0",
)
"""Text that means the session is not being honoured.

Distinguished from a challenge because the remedy differs: a stale session is
fixed by exporting a new one, a challenge is not fixed by anything this program
should do.
"""


@dataclass(frozen=True, slots=True)
class Download:
    """One downloadable rendering of a score."""

    kind: str
    """``pdf``, ``mscz``, ``mid`` and so on, as MuseScore names them."""

    url: str
    """The address, including the per-score token that authorises it."""


@dataclass(frozen=True, slots=True)
class ScorePage:
    """What one score page disclosed about itself.

    Everything is optional except the score number and the downloads, because
    everything else is decoration on a page that has already answered the two
    questions worth asking: what can be fetched, and may it be fetched today.
    """

    score_id: str
    downloads: tuple[Download, ...]
    title: str | None = None
    composer: str | None = None
    author: str | None = None
    pages: int | None = None
    daily_limit: int | None = None
    """What the host says the allowance is, when it says so.

    Read rather than assumed. A configured limit is a guess about somebody
    else's rule; this is the rule.
    """

    limit_reached: bool = False
    """``True`` when the host says today's allowance is already spent."""

    def download_for(self, kind: str) -> Download | None:
        """Return the rendering called *kind*, if the page offered it."""
        for download in self.downloads:
            if download.kind == kind:
                return download
        return None


def parse_score_page(html: str, *, url: str) -> ScorePage:
    """Return the state embedded in *html*.

    Raises:
        ChallengeEncounteredError: a bot check answered instead of the page.
        SessionExpiredError: the page came back logged out.
        ScorePageError: the page is neither of those and holds no state.
    """
    lowered = html.lower()
    if any(marker in lowered for marker in CHALLENGE_MARKERS):
        msg = f"a bot check answered instead of {url}"
        raise ChallengeEncounteredError(msg)
    state = _find_state(html)
    if state is None:
        if any(marker in lowered for marker in LOGIN_MARKERS):
            msg = f"the session was not honoured at {url}"
            raise SessionExpiredError(msg)
        msg = f"no score state found in the page at {url}"
        raise ScorePageError(msg)
    if not _is_authenticated(state):
        msg = f"the session was not honoured at {url}"
        raise SessionExpiredError(msg)
    return _read(state, url=url)


def _find_state(html: str) -> Mapping[str, Any] | None:
    """Return the first ``data-`` attribute that decodes to a state document.

    Several attributes match the shape on a given page. Trying each and keeping
    the one with a ``store`` is cheaper than being clever about which, and it
    is what keeps this working when the page grows another widget.
    """
    for match in STATE_ATTRIBUTE.finditer(html):
        try:
            document = json.loads(unescape(match.group(1)))
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(document, dict) and isinstance(document.get("store"), dict):
            return document
    return None


def _is_authenticated(state: Mapping[str, Any]) -> bool:
    """Return whether the page was rendered for a signed-in reader.

    The page says so in two independent places. Neither being present is not
    taken as a refusal: a missing field means the page changed, which is a
    different complaint with a different fix.
    """
    config = _mapping(state, "config")
    tags = _mapping(config, "analytics_handler_payload")
    if "isAuthenticated" in tags and not tags.get("isAuthenticated"):
        return False
    user = _mapping(_mapping(state, "store"), "user")
    return not (user and user.get("id") in (None, 0, ""))


def _read(state: Mapping[str, Any], *, url: str) -> ScorePage:
    """Return the fields worth keeping out of a decoded state document."""
    data = _mapping(_mapping(_mapping(state, "store"), "page"), "data")
    score = _mapping(data, "score")
    score_id = score.get("id")
    if score_id in (None, ""):
        msg = f"the state at {url} names no score"
        raise ScorePageError(msg)
    downloads = _downloads(data.get("type_download_list"))
    if not downloads:
        msg = f"the state at {url} offers no downloads"
        raise ScorePageError(msg)
    return ScorePage(
        score_id=str(score_id),
        downloads=downloads,
        title=_text(score.get("title")),
        composer=_text(score.get("composer_name")) or _text(score.get("artist_name")),
        author=_text(_mapping(score, "user").get("name")),
        pages=_count(score.get("pages_count")),
        daily_limit=_count(data.get("limit_download_count")),
        limit_reached=bool(data.get("is_download_limited")),
    )


def _downloads(raw: object) -> tuple[Download, ...]:
    """Return the renderings named in a ``type_download_list``.

    Entries without both a type and a URL are dropped rather than complained
    about: the list has carried placeholder shapes before, and one odd row is
    not a reason to refuse a page that offers six good ones.
    """
    if not isinstance(raw, list):
        return ()
    found: list[Download] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = _text(item.get("type"))
        address = _text(item.get("url"))
        if kind and address:
            found.append(Download(kind=kind, url=address))
    return tuple(found)


def _mapping(source: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """Return the mapping at *key*, or an empty one for anything else."""
    value = source.get(key)
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str | None:
    """Return *value* as a non-empty string, or ``None``."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _count(value: object) -> int | None:
    """Return *value* as a non-negative count, or ``None``."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
