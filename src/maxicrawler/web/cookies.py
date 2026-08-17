"""A session somebody else established, held so it reaches one host and no other.

MaxiCrawler does not log in. It has no password to type, no form to submit and
no intention of acquiring either: the account this was built for signs in
through Apple, and a provider that performed a login would be a provider that
holds credentials, which is a different program with different obligations.
What it does instead is accept a session a person already has — exported from
their own browser, for their own account — and put it back on the wire.

That makes the jar a **carrier, not an authenticator**. It cannot obtain a
session, refresh one, or notice that one has been revoked; the provider learns
that from what the host answers, which is the only place the truth lives.

**Three shapes, because three ways of getting one are normal.** A browser
extension writes the Netscape ``cookies.txt`` format. A person told to copy the
``Cookie:`` line copies that line. And a person who selects the request-headers
panel in Edge's developer tools and presses copy gets something different
again: each header's *name* on one line and its *value* on the next, blank
lines between. :meth:`CookieJar.from_text` tells all three apart by looking,
which keeps the instruction to the person short — and the developer-tools route
is worth supporting properly because it needs nothing installed, and because
the session cookies are ``HttpOnly``, so that panel is the *only* view of the
browser that shows all of them.

Getting the shapes wrong here is not a parse error, it is a silent one: a
``User-Agent`` contains semicolons, so a reader that split the whole file on
them would turn ``Windows NT 10.0; Win64; x64`` into three cookies and send
them.

**The user agent is kept when the file carries one**, because a session and the
browser it was issued to are one fact. A bot check binds its cookie to the
browser that earned it; replaying that cookie under a different name is
presenting it in circumstances that no longer match. Reading both from one file
is what stops them drifting apart.

**Why the domain is a constructor argument rather than a filter applied later.**
A jar is built *for* a host. :meth:`header_for` answers with the session only
when asked about that host, so a transport wired to the wrong URL gets nothing
rather than leaking an account to whoever the wrong URL belongs to. The check
is a property of the object, so it holds no matter who calls it.

**Why plaintext is refused.** A redirect from ``https`` to ``http`` is the
cheapest way to read a session off the wire, and a jar that answered such a
request would supply the very thing being fished for. Loopback is exempt: a
test server on ``127.0.0.1`` is not a network, and the alternative — tests that
cannot exercise the real path — buys nothing.

The value itself lives in :class:`~maxicrawler.domain.providers.ResourceSecret`,
which redacts itself in every rendering. This module is therefore one of the
few allowed to call :meth:`~maxicrawler.domain.providers.ResourceSecret.reveal`,
and ``tests/test_session_confinement.py`` pins that list down to exactly the
modules that should be on it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from maxicrawler.domain.providers import ResourceSecret

Clock = Callable[[], datetime]
"""A source of the current time. Injected so an expiry test needs no real one."""

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
"""Hosts where plaintext is not a downgrade, because there is no wire to read."""

HTTPONLY_PREFIX = "#HttpOnly_"
"""What curl and several browser extensions write in front of an HttpOnly line.

It looks like a comment and is not one. Treating it as one would silently drop
exactly the cookies that carry the session, which is the failure that would be
hardest to diagnose from the outside: a jar that parsed cleanly and did not
work.
"""

NETSCAPE_FIELDS = 7
"""Domain, subdomains flag, path, secure flag, expiry, name, value."""

HEADER_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*)\s*:\s*(.*)$")
"""``name: value`` on one line, the way a header is usually written down."""

HEADER_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]*$")
"""A bare header name on a line of its own, the way Edge's panel copies.

Deliberately strict. A cookie line is full of ``=`` and ``;`` and a URL is full
of ``/``, so neither can be mistaken for a name — which matters because names
and values are told apart by shape alone here.
"""


class CookieError(ValueError):
    """Raised when text meant to carry a session does not."""


@dataclass(frozen=True, slots=True)
class CookieJar:
    """The cookies for one host, and the rule about who may have them.

    Immutable, so a jar handed to a transport cannot be widened underneath it.
    """

    domain: str
    """The host this session belongs to, without a leading dot."""

    names: tuple[str, ...]
    """Which cookies are held, in the order given.

    Names, never values. A status page wants to say *"the session is here and
    it includes ``cf_clearance``"* without putting an account on screen, and
    this is what lets it.
    """

    header: ResourceSecret
    """The assembled ``Cookie:`` value, redacted in every rendering."""

    user_agent: str | None = None
    """The browser the session was exported from, when the file said.

    Not a secret and deliberately not wrapped: it is one of the least private
    strings a browser sends. It lives here because it belongs to the same
    export as the cookies, and keeping the pair together is what stops a
    session being replayed under a name it was never issued to.
    """

    @classmethod
    def from_file(cls, path: Path, *, domain: str, clock: Clock | None = None) -> CookieJar:
        """Read a session from *path*, in either supported shape.

        Raises :class:`CookieError` when the file is missing, unreadable, or
        holds nothing for *domain* — all three mean the same thing to the
        person who has to fix it, and none of them should look like a session
        that merely has not been tried yet.
        """
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            msg = f"cannot read the cookie file at {path}: {error}"
            raise CookieError(msg) from error
        try:
            return cls.from_text(text, domain=domain, clock=clock)
        except CookieError as error:
            msg = f"{path}: {error}"
            raise CookieError(msg) from error

    @classmethod
    def from_text(cls, text: str, *, domain: str, clock: Clock | None = None) -> CookieJar:
        """Read a session from *text*, telling the shapes apart by looking.

        A Netscape line is seven tab-separated fields, and nothing in a header
        block is tab-separated, so one tab-bearing line settles that half.
        Otherwise the text is read as headers; a ``cookie`` among them means it
        really was a header block, and its absence means the whole text is the
        cookie line itself.

        Deciding by content means the person exporting a session never has to
        say which button they pressed.
        """
        if any(line.count("\t") >= NETSCAPE_FIELDS - 1 for line in text.splitlines()):
            return cls.from_netscape(text, domain=domain, clock=clock)
        headers = read_headers(text)
        if "cookie" in headers:
            return cls._build(
                _pairs_in(headers["cookie"]),
                domain=domain,
                user_agent=headers.get("user-agent"),
            )
        return cls.from_header_line(text, domain=domain)

    @classmethod
    def from_header_line(cls, line: str, *, domain: str) -> CookieJar:
        """Read a session from one ``Cookie:`` request header.

        A leading ``Cookie:`` is tolerated because copying the whole line is
        what a person does when told to copy the line.
        """
        stripped = line.strip()
        if stripped.lower().startswith("cookie:"):
            stripped = stripped.split(":", 1)[1].strip()
        return cls._build(_pairs_in(stripped), domain=domain)

    @classmethod
    def from_netscape(cls, text: str, *, domain: str, clock: Clock | None = None) -> CookieJar:
        """Read a session from the Netscape ``cookies.txt`` format.

        Cookies for other domains are dropped rather than refused: an export is
        usually the whole browser, and complaining about the other nine hosts
        in it would be complaining about normal input. Expired cookies go the
        same way, which is why the clock is injectable.
        """
        now = (clock() if clock is not None else datetime.now(UTC)).timestamp()
        pairs: list[tuple[str, str]] = []
        for raw in text.splitlines():
            line = raw[len(HTTPONLY_PREFIX) :] if raw.startswith(HTTPONLY_PREFIX) else raw
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) < NETSCAPE_FIELDS:
                continue
            host, _, _, _, expiry, name, value = (
                field.strip() for field in fields[:NETSCAPE_FIELDS]
            )
            if not name or not _covers(host.lstrip("."), domain):
                continue
            if _has_expired(expiry, now):
                continue
            pairs.append((name, value))
        return cls._build(pairs, domain=domain)

    @classmethod
    def _build(
        cls,
        pairs: list[tuple[str, str]],
        *,
        domain: str,
        user_agent: str | None = None,
    ) -> CookieJar:
        """Assemble a jar, refusing one that would hold nothing.

        An empty jar is not a session with no cookies in it; it is a mistake
        somewhere upstream — the wrong file, the wrong domain, a logged-out
        browser — and it should be said at the point it can still be fixed
        rather than at the first request that fails.
        """
        host = domain.strip().lstrip(".").lower()
        if not host:
            msg = "a cookie jar needs the host it belongs to"
            raise CookieError(msg)
        if not pairs:
            msg = f"no cookies for {host} were found"
            raise CookieError(msg)
        return cls(
            domain=host,
            names=tuple(name for name, _ in pairs),
            header=ResourceSecret("; ".join(f"{name}={value}" for name, value in pairs)),
            user_agent=user_agent or None,
        )

    def header_for(self, url: str) -> str | None:
        """Return the ``Cookie:`` value for *url*, or ``None`` for anywhere else.

        ``None`` rather than an exception: a transport asking about a URL this
        jar has no business with is the ordinary case — a redirect to a CDN, an
        asset on another host — and it wants to proceed without the session,
        not to fail.

        This is the one place the wrapped value is unwrapped, and it is late:
        the string exists only as long as the request being built.
        """
        split = urlsplit(url)
        host = (split.hostname or "").lower()
        if not _covers(host, self.domain):
            return None
        if split.scheme != "https" and host not in LOOPBACK_HOSTS:
            return None
        return self.header.reveal()


def read_headers(text: str) -> dict[str, str]:
    """Return the request headers written down in *text*, lowercased by name.

    Two layouts, read in one pass. ``name: value`` on a line is the usual way a
    header is written. A line that is *only* a name belongs to the other one:
    Edge's request-headers panel copies each name and value onto separate
    lines, with blank lines between, and the value is then the next line that
    has anything on it.

    Reading both in order is what keeps them from confusing each other. A
    referer's value is a URL, which looks exactly like ``name: value`` — but by
    the time it is reached it has already been consumed as the value belonging
    to the name above it, so it is never mistaken for a header of its own.

    The first spelling of a name wins, because a file somebody appended to
    twice should keep working like the session it was the first time.
    """
    headers: dict[str, str] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line:
            continue
        written = HEADER_LINE.match(line)
        if written is not None:
            headers.setdefault(written.group(1).lower(), written.group(2).strip())
            continue
        if HEADER_NAME.match(line) is None:
            continue
        while index < len(lines) and not lines[index].strip():
            index += 1
        if index < len(lines):
            headers.setdefault(line.lower(), lines[index].strip())
            index += 1
    return headers


def _pairs_in(cookie: str) -> list[tuple[str, str]]:
    """Return the name and value of every cookie in a ``Cookie:`` value."""
    pairs: list[tuple[str, str]] = []
    for part in cookie.split(";"):
        candidate = part.strip()
        if not candidate or "=" not in candidate:
            continue
        name, _, value = candidate.partition("=")
        if name.strip():
            pairs.append((name.strip(), value.strip()))
    return pairs


def _covers(host: str, domain: str) -> bool:
    """Return whether *host* is *domain* or sits beneath it.

    ``ja.musescore.com`` is the same account as ``musescore.com``; ``evil.com``
    and ``musescore.com.evil.com`` are not, and the dot is what keeps the
    suffix check from saying otherwise.
    """
    host = host.lower().lstrip(".")
    return host == domain or host.endswith(f".{domain}")


def _has_expired(expiry: str, now: float) -> bool:
    """Return whether a Netscape expiry field is in the past.

    ``0`` means a session cookie, which has no expiry and outlives nothing but
    the browser it came from — that is exactly the cookie worth carrying, so it
    is never treated as stale. An unparsable field is kept for the same reason
    a missing one is: dropping a cookie because its timestamp was odd would
    break a session over a detail no host actually checks.
    """
    try:
        seconds = float(expiry)
    except ValueError:
        return False
    return 0 < seconds < now
