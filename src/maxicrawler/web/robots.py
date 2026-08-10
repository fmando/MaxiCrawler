"""What one ``/robots.txt`` says, and how to ask it.

This module is the only place in MaxiCrawler that knows how a ``robots.txt``
document is written. Everything above it asks two questions — *"may this
crawler fetch this URL?"* and *"how long should it wait?"* — and gets a value
back.

**The matching is Protego's, not ours.** RFC 9309 requires wildcards (``*``),
an end-of-match anchor (``$``), longest-match precedence with ``Allow``
breaking ties, and group selection by product token. The standard library's
:mod:`urllib.robotparser` predates all of that and compares paths with
``startswith``, so ``Disallow: /*.pdf$`` matches nothing there — it would make
us *under*-obey while believing we obey. Protego implements the modern rules,
is pure Python with no dependencies of its own, ships type information, and is
maintained by the Scrapy project. ADR-029 records the evaluation.

**The I/O is ours.** Protego parses a string and never opens a socket; fetching
is :class:`~maxicrawler.web.robots.RobotsPolicy`'s job, through the same
:class:`~maxicrawler.web.fetcher.PageFetcher` protocol a crawl already uses. So
there is no second way out of this process, and a test drives the whole thing
without a network.

Three details are ours rather than the library's:

*   **Decoding.** RFC 9309 declares ``robots.txt`` to be UTF-8 regardless of
    what the server announces, and says a byte-order mark must be ignored.
    Protego does not strip one, and a document whose first line is
    ``\\ufeffUser-agent: *`` would lose its first group and silently permit
    everything. :func:`decode_robots` is where that is handled.
*   **The product token.** A ``User-Agent`` header carries a version;
    a ``robots.txt`` group names a product. ``MaxiCrawler/0.1.0`` is matched as
    ``MaxiCrawler``.
*   **The size bound.** RFC 9309 asks a crawler to parse at least 500 KiB and
    permits it to ignore the rest, so a robots.txt is fetched under a limit of
    its own rather than under the one meant for pages.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from protego import Protego

ROBOTS_PATH = "/robots.txt"
"""Where the rules live, on every host, by definition."""

MAX_ROBOTS_BYTES = 512 * 1024
"""How much of a ``robots.txt`` is read. RFC 9309 asks for at least 500 KiB."""

ROBOTS_MEDIA_TYPES = frozenset({"text/plain"})
"""What a ``robots.txt`` is supposed to be served as.

A server that answers with ``text/html`` is answering with a page — usually a
soft 404 — and a page is not a rule set. That is treated as *"this host
published no robots.txt"* rather than as an error, which is the reading RFC
9309 gives an unavailable one.
"""

_DENY_EVERYTHING = "User-agent: *\nDisallow: /\n"
"""The rules a host that could not be reached is treated as having.

Written as a document rather than as a flag so that :class:`RobotsRules` has
one code path and the strictest case is exercised by the same matcher as every
other case.
"""


def product_token(user_agent: str) -> str:
    """Return the name a ``robots.txt`` group would use for *user_agent*.

    ``robots.txt`` names products; a ``User-Agent`` header names a product
    *and* a version, and a group written for ``MaxiCrawler`` must apply to
    ``MaxiCrawler/0.1.0``. Everything from the first ``/`` or space is
    therefore dropped.

    An agent string that leaves nothing behind falls back to ``*``, which is
    the group every crawler is covered by — the safe direction, because it can
    only ever match *more* rules than a name would.
    """
    head = user_agent.strip().split("/", 1)[0].split()
    return head[0] if head else "*"


def origin_of(url: str) -> str:
    """Return the scheme, host, and port *url* belongs to.

    The unit ``robots.txt`` applies to, and therefore the key it is cached
    under. A default port is dropped so that ``https://example.org`` and
    ``https://example.org:443`` are one origin rather than two — the same rule
    :func:`~maxicrawler.utils.urls.normalize_url` applies, for the same reason.

    Raises:
        ValueError: *url* names no host.
    """
    parsed = urlsplit(url)
    host = parsed.hostname
    if not host:
        msg = f"cannot read a host from: {url}"
        raise ValueError(msg)
    scheme = parsed.scheme.lower()
    port = parsed.port
    default = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if port is None or default else f"{host}:{port}"
    return urlunsplit((scheme, netloc, "", "", ""))


def robots_url(url: str) -> str:
    """Return where the ``robots.txt`` governing *url* is published.

    Raises:
        ValueError: *url* names no host.
    """
    return f"{origin_of(url)}{ROBOTS_PATH}"


def decode_robots(body: bytes) -> str:
    """Return *body* as text, the way RFC 9309 says to read it.

    UTF-8 whatever the server announced, with a byte-order mark removed and
    undecodable bytes replaced rather than fatal. A rule file is a set of
    independent lines: one bad byte must cost that line, not the file — and
    certainly not the whole restriction, which is what an exception here would
    quietly do.
    """
    return body.decode("utf-8-sig", errors="replace")


@dataclass(frozen=True, slots=True)
class RobotsRules:
    """What one ``/robots.txt`` says, asked one URL at a time.

    A value: it holds no host, performs no I/O, and can be shared. Which origin
    it came from is :class:`RobotsPolicy`'s bookkeeping, because the same
    document says different things to different crawlers and nothing about
    where it was found.
    """

    _rules: Protego

    @classmethod
    def parse(cls, body: bytes | str) -> "RobotsRules":
        """Return the rules stated by a ``robots.txt`` document."""
        text = decode_robots(body) if isinstance(body, bytes) else body
        return cls(Protego.parse(text))

    @classmethod
    def unrestricted(cls) -> "RobotsRules":
        """Return the rules of a host that published none.

        What a 404 means, and what an unreadable document is treated as: no
        restrictions. RFC 9309 is explicit that an unavailable ``robots.txt``
        leaves a crawler free.
        """
        return cls.parse("")

    @classmethod
    def forbidding(cls) -> "RobotsRules":
        """Return the rules of a host whose ``robots.txt`` could not be reached.

        Not the same as absent. A 5xx or a timeout means we do not *know* what
        the host allows, and RFC 9309 says to assume complete disallow rather
        than to help ourselves to the benefit of the doubt.
        """
        return cls.parse(_DENY_EVERYTHING)

    def allows(self, url: str, *, token: str) -> bool:
        """Return whether a crawler called *token* may fetch *url*."""
        return bool(self._rules.can_fetch(url, token))

    def crawl_delay(self, token: str) -> float | None:
        """Return the delay this file asks *token* to keep, if it asks for one.

        Reported as it was written. Whether that number is honoured, and how
        far it may go, is a decision this value does not get to make.
        """
        delay = self._rules.crawl_delay(token)
        return None if delay is None else float(delay)
