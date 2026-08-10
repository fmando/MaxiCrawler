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
from threading import Lock
from urllib.parse import urlsplit, urlunsplit

from protego import Protego

from maxicrawler.web.errors import FetchError, HttpStatusError, TransportError
from maxicrawler.web.fetcher import PageFetcher
from maxicrawler.web.policy import PolicyDecision, PolicyRule

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

_SERVER_ERROR = 500
"""The status at which "unavailable" becomes "unreachable" in RFC 9309."""

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


class RobotsPolicy:
    """Asks each host's ``/robots.txt`` whether a URL may be fetched.

    A :class:`~maxicrawler.web.policy.CrawlPolicy`, and one that makes requests
    — so it belongs at the engine's *second* gate, where it is asked only about
    URLs that are genuinely next. At the first gate it would be asked about
    every URL a page mentions, and one page linking to three hundred domains
    would cost three hundred requests.

    Reads through an injected :class:`~maxicrawler.web.fetcher.PageFetcher`, so
    there is no second way out of this process and a test drives it without a
    socket. That fetcher should be built for this: ``text/plain`` rather than
    HTML, and :data:`MAX_ROBOTS_BYTES` rather than a page's limit.

    One document per origin, fetched at most once. The cache has no expiry
    because a crawl builds its own object graph and lives minutes, not days; a
    process that ever holds one for longer wants a TTL here, and nothing else.
    """

    def __init__(
        self,
        fetcher: PageFetcher,
        *,
        user_agent: str,
        deny_on_error: bool = True,
        max_delay: float | None = None,
    ) -> None:
        if max_delay is not None and max_delay < 0:
            msg = "max_delay must not be negative"
            raise ValueError(msg)
        self._fetcher = fetcher
        self._token = product_token(user_agent)
        self._deny_on_error = deny_on_error
        self._max_delay = max_delay
        self._lock = Lock()
        self._cache: dict[str, RobotsRules] = {}

    @property
    def token(self) -> str:
        """Return the name this crawler is matched under."""
        return self._token

    def may_fetch(self, url: str) -> PolicyDecision:
        """Return whether the host of *url* permits fetching it."""
        try:
            origin = origin_of(url)
        except ValueError:
            # Not a URL with a host, so not one robots.txt has an opinion
            # about. Refusing it is somebody else's job, and doing it here
            # would report the wrong reason for it.
            return PolicyDecision.allow()
        if self._rules_for(origin).allows(url, token=self._token):
            return PolicyDecision.allow()
        return PolicyDecision.refuse(f"disallowed by {origin}{ROBOTS_PATH}", rule=PolicyRule.ROBOTS)

    def delay_for(self, url: str) -> float | None:
        """Return how long the host of *url* asks this crawler to wait.

        ``None`` when it asks for nothing. A value larger than *max_delay* is
        clamped to it: a ``Crawl-delay: 86400`` is a rule this crawler cannot
        honour and stay a program somebody uses, and reading it literally would
        turn one hostile line into a frozen crawl.

        Answered from the cache, so asking costs a request only if the URL's
        host has not been consulted yet — which for a throttle it always has,
        because the fetch it is spacing was permitted by :meth:`may_fetch`
        first.
        """
        try:
            origin = origin_of(url)
        except ValueError:
            return None
        delay = self._rules_for(origin).crawl_delay(self._token)
        if delay is None:
            return None
        return delay if self._max_delay is None else min(delay, self._max_delay)

    def _rules_for(self, origin: str) -> RobotsRules:
        """Return the rules *origin* published, fetching them once.

        The lock guards the cache rather than the fetch: two crawls sharing one
        policy may briefly ask the same host twice, which costs a request. A
        lock held across the fetch would instead let one slow host stall every
        thread that wanted a different one.
        """
        with self._lock:
            cached = self._cache.get(origin)
        if cached is not None:
            return cached
        rules = self._read(origin)
        with self._lock:
            self._cache.setdefault(origin, rules)
            return self._cache[origin]

    def _read(self, origin: str) -> RobotsRules:
        """Fetch and interpret the ``robots.txt`` of *origin*.

        The whole of RFC 9309's status handling, and the one place a *failure*
        turns into a *permission*:

        *   **2xx** — the rules it states.
        *   **4xx**, including 401 and 403 — "unavailable". No restrictions;
            the RFC is explicit that a crawler may then access any resource.
        *   **anything unreadable** — not text, too large, broken encoding, a
            redirect chain that never resolved. Content we declined to read is
            not a server saying no, so it reads as unavailable too.
        *   **5xx, a timeout, a refused connection** — "unreachable". We do not
            *know* what this host allows, and the RFC says to assume complete
            disallow rather than to take the benefit of the doubt.

        The last case is the only one that can stop a crawl over something
        other than a rule, which is why ``deny_on_error`` can invert it.
        """
        try:
            page = self._fetcher.fetch(f"{origin}{ROBOTS_PATH}")
        except HttpStatusError as error:
            unreachable = error.status >= _SERVER_ERROR
            return self._unreachable() if unreachable else RobotsRules.unrestricted()
        except TransportError:
            return self._unreachable()
        except FetchError:
            # Not a page we can read: the wrong media type, more bytes than we
            # will hold, a broken body, a chain that never resolved.
            return RobotsRules.unrestricted()
        return RobotsRules.parse(page.body)

    def _unreachable(self) -> RobotsRules:
        """Return what a host we could not reach is treated as having said."""
        return RobotsRules.forbidding() if self._deny_on_error else RobotsRules.unrestricted()
