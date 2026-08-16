"""Score pages built here rather than captured from MuseScore.

The repository carries no third-party content, which is the same rule the Mega
fixtures follow: every page a test reads is assembled from the shape the parser
claims to understand, so a test that passes proves the parser handles that
shape rather than proving a saved file still exists.

The shape itself is the part worth stating plainly. A score page hands the
browser its whole starting state in one ``data-<digest>`` attribute holding
HTML-escaped JSON, and the fields that matter sit at
``store.page.data`` — the download list, the daily allowance, and whether the
allowance is spent.
"""

from __future__ import annotations

import json
from html import escape
from typing import Any

DIGEST = "e22a377651bdb329588e37afd113db9b3f887e45a781fa440c0109b8fffc890d"
"""A stand-in for the per-deployment digest the real attribute name carries.

Its value is irrelevant on purpose: the parser must find the attribute by shape
rather than by name, and a fixture that used a memorable name would let a
name-matching parser pass.
"""

SCORE_ID = "4217351"
TITLE = "Study in Four Bars"
AUTHOR = "an arranger"
COMPOSER = "a composer"


def download_list(*kinds: str, score_id: str = SCORE_ID) -> list[dict[str, str]]:
    """Return a ``type_download_list`` offering *kinds*."""
    return [
        {
            "type": kind,
            "url": f"https://musescore.com/score/download/index?score_id={score_id}&type={kind}",
        }
        for kind in kinds
    ]


def state(
    *,
    score_id: str = SCORE_ID,
    title: str | None = TITLE,
    kinds: tuple[str, ...] = ("mscz", "pdf", "mid"),
    daily_limit: int | None = 20,
    limit_reached: bool = False,
    authenticated: bool = True,
    pages: int | None = 2,
) -> dict[str, Any]:
    """Return a state document shaped like the one a score page carries."""
    score: dict[str, Any] = {
        "id": int(score_id),
        "user": {"id": 21965011, "name": AUTHOR},
        "artist_name": COMPOSER,
        "composer_name": "",
    }
    if title is not None:
        score["title"] = title
    if pages is not None:
        score["pages_count"] = pages
    data: dict[str, Any] = {
        "score": score,
        "type_download_list": download_list(*kinds, score_id=score_id),
        "is_download_limited": limit_reached,
    }
    if daily_limit is not None:
        data["limit_download_count"] = daily_limit
    return {
        "config": {
            "analytics_handler_payload": {"country": "DE", "isAuthenticated": authenticated}
        },
        "store": {
            "user": {"id": 75224200 if authenticated else 0, "hasProAccess": 1},
            "page": {"data": data},
        },
    }


def page(*, document: dict[str, Any] | None = None, extra: str = "") -> str:
    """Return the markup of a score page carrying *document* as its state."""
    payload = escape(json.dumps(document if document is not None else state()), quote=True)
    return (
        "<!DOCTYPE html><html><head><title>a score</title></head><body>"
        f'<div class="js-page"></div><div data-{DIGEST}="{payload}"></div>'
        f"{extra}</body></html>"
    )


def challenge_page() -> str:
    """Return what a bot check answers with instead of a score page."""
    return (
        "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>"
        '<div id="cf-challenge"></div>'
        '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>'
        "</body></html>"
    )


def login_page() -> str:
    """Return what the site answers with when the session is not honoured."""
    return '<!DOCTYPE html><html><body><a href="/user/login">Log in</a></body></html>'
