"""What one crawl is, what it was told to do, and how it is stopped.

"Session" names two different things in a crawler, and keeping them apart is
the whole point of this module:

*   the **run** — an identity, a seed, the options it was given, when it
    started. That is :class:`CrawlSession`, and it is what gets summarized,
    serialized and stored.
*   the **request context** — how each request is made: the user agent, extra
    headers, and later a cookie jar, a credential, a proxy. That is
    :class:`RequestContext`.

Folding the second into the first would put a cookie jar inside the object that
becomes a JSON document and a database row. They are therefore separate, and
:class:`CrawlSession` holds the context without ever looking inside it — the
crawler must not know *how* authentication works, only that a fetcher was
handed something that arranges it.

Outlook: a :class:`CrawlSession` will most likely become one part of a larger
**crawl job** — a job holding a session, its discovery results, a download
queue and a result. This module is the session, not the job; the wider shape is
sketched in ``docs/architecture.md``.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from threading import Event

from maxicrawler.domain import ScanSession

DEFAULT_MAX_PAGES = 50
"""How many pages one crawl fetches unless told otherwise.

A ceiling rather than a target. With ``--same-domain`` off by default, this and
``--depth`` are what keep a recursive crawl finite.
"""


class CrawlState(StrEnum):
    """Where a crawl is, and — once it is over — why it ended.

    The terminal values *are* the reason, so no second "stop reason" concept is
    needed to explain a report.
    """

    PENDING = "pending"
    """Built, not started."""

    RUNNING = "running"
    """The loop is turning."""

    COMPLETED = "completed"
    """The frontier ran dry; everything in scope was visited."""

    PAGE_LIMIT = "page_limit"
    """The page ceiling was reached; the frontier still holds work."""

    INTERRUPTED = "interrupted"
    """A stop was requested, by Ctrl-C or by a caller."""

    @property
    def is_finished(self) -> bool:
        """Return whether this is a terminal state."""
        return self in {CrawlState.COMPLETED, CrawlState.PAGE_LIMIT, CrawlState.INTERRUPTED}


@dataclass(frozen=True, slots=True)
class CrawlOptions:
    """What a crawl was told to do.

    ``same_domain`` is off by default on purpose. MaxiCrawler serves two
    workflows equally: crawling one website, where staying on it is what you
    want, and hunting for share links, where the interesting URLs are on
    *other* hosts — Mega, Pixeldrain, GoFile. Restricting by default would
    quietly break the second. ``max_depth`` and ``max_pages`` are what bound a
    crawl instead.
    """

    max_depth: int = 0
    """Link distance from the seed. Zero fetches the seed alone."""

    max_pages: int = DEFAULT_MAX_PAGES
    same_domain: bool = False
    include_subdomains: bool = False
    """Whether ``docs.example.org`` counts as inside ``example.org``."""

    scan_prose: bool = True

    def __post_init__(self) -> None:
        if self.max_depth < 0:
            msg = "max_depth must not be negative"
            raise ValueError(msg)
        if self.max_pages < 1:
            msg = "max_pages must be at least 1"
            raise ValueError(msg)

    @property
    def is_recursive(self) -> bool:
        """Return whether this crawl follows links at all."""
        return self.max_depth > 0


@dataclass(frozen=True, slots=True)
class RequestContext:
    """How requests are made, held opaquely by the crawl that uses it.

    Today it carries a user agent and extra headers. It is the declared home of
    what comes next — a cookie jar, a credential, a proxy — and the reason it
    exists now is that those must never end up on :class:`CrawlSession` itself,
    where they would flow into a report, a JSON document and a database row.

    The division of labour with the fetcher is deliberate: **this holds the
    data, a fetcher decorator holds the behaviour.** Performing a login,
    refreshing a CSRF token, retrying a 401 — all of that belongs to something
    wrapping a :class:`~maxicrawler.web.fetcher.PageFetcher`, which is already
    a protocol. Neither the engine nor the discovery service changes when it
    arrives.
    """

    user_agent: str
    headers: tuple[tuple[str, str], ...] = ()
    """Extra request headers, as ordered pairs so the context stays hashable."""

    @classmethod
    def of(cls, *, user_agent: str, headers: Mapping[str, str] | None = None) -> "RequestContext":
        """Return a context carrying *headers* in a stable order."""
        return cls(
            user_agent=user_agent,
            headers=tuple(sorted((headers or {}).items())),
        )

    def header_map(self) -> dict[str, str]:
        """Return the extra headers as a mapping."""
        return dict(self.headers)


@dataclass(frozen=True, slots=True)
class CrawlSession:
    """One crawl: what it started from, what it was told, and when.

    Immutable, like everything else that describes rather than does. The parts
    that change while a crawl runs live on :class:`CrawlControl`, and the parts
    that describe how it ended live on
    :class:`~maxicrawler.web.report.CrawlReport`.
    """

    session_id: str
    seed_url: str
    started_at: datetime
    options: CrawlOptions = field(default_factory=CrawlOptions)
    context: RequestContext = field(
        default_factory=lambda: RequestContext(user_agent="MaxiCrawler")
    )

    @property
    def scan_session(self) -> ScanSession:
        """Return the discovery session this crawl feeds.

        The same identifier, so one crawl is one row in ``crawl_sessions`` and
        one row in ``scan_sessions``, joined without a second key — and every
        URL it discovers is already reachable from the crawl that found it.
        """
        return ScanSession(session_id=self.session_id, started_at=self.started_at)


class CrawlControl:
    """The live state of a crawl, and the button that stops it.

    The one deliberately mutable object in the crawl model. It exists so that
    stopping is neither a signal handler — global process state, hostile to a
    library caller and awkward to test — nor a flag threaded through every
    method. A user interface holds one of these to read progress and to offer a
    Stop button; Ctrl-C in the terminal takes exactly the same path.

    Backed by an :class:`~threading.Event`, so a worker added later can be
    stopped from another thread without changing anything here.
    """

    __slots__ = ("_state", "_stop")

    def __init__(self) -> None:
        self._stop = Event()
        self._state = CrawlState.PENDING

    @property
    def state(self) -> CrawlState:
        """Return where the crawl currently is."""
        return self._state

    @state.setter
    def state(self, state: CrawlState) -> None:
        """Record where the crawl currently is."""
        self._state = state

    def request_stop(self) -> None:
        """Ask the crawl to stop after the page it is working on."""
        self._stop.set()

    @property
    def stop_requested(self) -> bool:
        """Return whether a stop has been asked for."""
        return self._stop.is_set()

    def __repr__(self) -> str:
        """Return a representation naming the state and the stop flag."""
        return f"{type(self).__name__}(state={self._state}, stop_requested={self.stop_requested})"
