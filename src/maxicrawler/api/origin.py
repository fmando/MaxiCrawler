"""Refusing a state-changing request that arrived from somewhere else.

This interface has no authentication and says so (ADR-025): whoever can reach
the port can use it. That was a bounded statement while the worst a stray
request could do was start a crawl. It stops being bounded the moment a button
deletes a file, because a page in any other tab can submit a form at this
server, and the browser will send it — that is what a cross-site request
forgery *is*, and it needs no access to anything this server returns.

So every unsafe method has to have come from a page of ours.

**How it is decided, in the order it is asked.**

``Sec-Fetch-Site`` is the answer when the browser gives one, and every current
browser does. It is set by the browser and unreachable from script, which is
exactly the property a token in a form field has to be given by hand. ``same-origin``
is one of our own pages; ``none`` is a user-initiated navigation and is accepted
because refusing it could only ever cost a legitimate request; ``same-site`` and
``cross-site`` are refused, the first because this server has no sibling hosts
that ought to be posting to it.

``Origin`` is the fallback for a client that sent no ``Sec-Fetch-*`` header at
all. A browser attaches it to every cross-origin POST, so a mismatch is a
refusal — compared against the ``Host`` the request was addressed to, because
the scheme is not knowable behind a reverse proxy and the host is.

**Neither header present means the request is allowed**, and that is a decision
rather than an oversight. A browser always sends at least one of them on a
cross-origin POST; what sends neither is ``curl``, a script, a test — something
already running on the machine, which is the situation the command line is in
anyway. Refusing those would break every non-browser client to protect against
an attacker who could simply not send the header either.

**What this is not.** It is not authentication and does not become any. It
stops a page somebody else wrote from acting through a browser that can reach
this server; it stops nobody who can make a request directly. That remains the
job of a reverse proxy that authenticates in front of this, and until the
interface has accounts of its own, binding anywhere but loopback stays a
deliberate act.

It is a *pure ASGI* middleware rather than a ``BaseHTTPMiddleware`` subclass,
and that is load-bearing: two routes here answer with an event stream that stays
open for the length of a crawl, and ``BaseHTTPMiddleware`` buffers a response
through a queue. This one inspects the request and then either steps out of the
way entirely or answers instead of the application, so a stream it lets through
is the application's own.
"""

from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
"""Methods that change nothing and are therefore never refused."""

ACCEPTED_SITES = frozenset({"same-origin", "none"})
"""The ``Sec-Fetch-Site`` values a request of ours can carry."""

REFUSAL = (
    "This request did not come from a MaxiCrawler page, so nothing was done.\n"
    "Open the interface directly and use the control there."
)
"""Said to whoever is refused; short, because the browser shows it verbatim."""


class SameOriginMiddleware:
    """Lets an unsafe request through only when it came from one of our pages."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass the request on, or answer it with a refusal.

        Anything that is not HTTP — the lifespan messages, and a WebSocket if
        one is ever added — is handed on untouched: this decides about methods,
        and those have none.
        """
        if scope["type"] != "http" or scope.get("method", "") in SAFE_METHODS:
            await self._app(scope, receive, send)
            return
        if is_ours(Headers(scope=scope)):
            await self._app(scope, receive, send)
            return
        await PlainTextResponse(REFUSAL, status_code=403)(scope, receive, send)


def is_ours(headers: Headers) -> bool:
    """Return whether a request carrying *headers* came from one of our pages.

    Separate from the middleware so the rule can be read and tested as a rule,
    rather than only through a client and a status code.
    """
    site = headers.get("sec-fetch-site")
    if site is not None:
        return site in ACCEPTED_SITES
    origin = headers.get("origin")
    if origin is None:
        return True
    return urlsplit(origin).netloc.casefold() == headers.get("host", "").casefold()
