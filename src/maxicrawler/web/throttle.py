"""Waiting between requests to one host.

*"May I fetch this?"* and *"may I fetch it **yet**?"* are different questions.
The first is a :class:`~maxicrawler.web.policy.CrawlPolicy`; the second is
this, and it is deliberately not a policy — a policy that answered "not yet"
would have to be asked again in a loop, and something would have to own that
loop. Waiting belongs where the request is made.

So politeness is a :class:`~maxicrawler.web.fetcher.PageFetcher` wrapping
another one, the same trick :class:`~maxicrawler.providers.retry.Retrier` plays
around the provider transport. The engine keeps knowing nothing about time,
the frontier keeps holding no timestamps, and there is no ``sleep`` anywhere
above this file.

**Why the schedule is a separate object.** ``RobotsPolicy`` needs a fetcher to
read ``/robots.txt``; a throttle needs ``RobotsPolicy`` to learn a host's
``Crawl-delay``. Wiring one into the other would close a loop. Splitting the
*state* out breaks it: both fetchers share one :class:`HostSchedule`, the page
fetcher asks robots for its delay, and the robots fetcher does not ask anybody.
The robots request is spaced like every other request without the file needing
to describe its own retrieval.

**Waiting is interruptible.** The waiter is injected, and ``serve`` passes
:meth:`~maxicrawler.web.session.CrawlControl.wait`, so a stop during a
thirty-second delay returns at once instead of holding a shutdown open. The
engine then ends the crawl at the next turn of its loop, which is where a crawl
has always ended.
"""

from collections.abc import Callable
from threading import Lock
from time import monotonic, sleep
from urllib.parse import urlsplit

from maxicrawler.web.fetcher import PageFetcher
from maxicrawler.web.models import FetchedPage

Clock = Callable[[], float]
"""A monotonic source of seconds. Injected so a test needs no real time."""

Waiter = Callable[[float], None]
"""Blocks for at most the seconds it is given, and may return sooner."""

DelaySource = Callable[[str], float | None]
"""Answers how long a host asks this crawler to wait, if it asks at all.

:meth:`~maxicrawler.web.robots.RobotsPolicy.delay_for` satisfies this, which is
the whole of the connection between politeness and ``robots.txt``: one
function, passed in, never imported.
"""


def host_key(url: str) -> str | None:
    """Return what counts as "the same server" for the purpose of waiting.

    Host and port, without the scheme: ``http://example.org`` and
    ``https://example.org`` are one machine answering on two ports and should
    not be hammered twice as fast for it. Subdomains are *not* folded together
    — ``cdn.example.org`` is usually somebody else's machine, and treating a
    whole domain as one server would slow a crawl down for no benefit to
    anyone.

    ``None`` for a URL with no host, which is not something this can space out.
    """
    parsed = urlsplit(url)
    if not parsed.hostname:
        return None
    return f"{parsed.hostname.lower()}:{parsed.port}" if parsed.port else parsed.hostname.lower()


class HostSchedule:
    """When each host may next be asked for something.

    Shared by every fetcher that talks to the same hosts, so that a crawl's
    politeness is not divided by the number of fetchers it happens to be built
    from.

    Guarded, because being asked from two threads is the case it exists for.
    """

    __slots__ = ("_clock", "_lock", "_next")

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock if clock is not None else monotonic
        self._lock = Lock()
        self._next: dict[str, float] = {}

    def reserve(self, url: str, delay: float) -> float:
        """Claim the next slot for the host of *url* and return the wait for it.

        The slot is booked *before* the caller waits, not after it returns, so
        two threads reaching the same host queue up behind each other instead
        of both deciding they may go now.

        The spacing is between the moments requests are *sent*. A fetch that
        takes longer than the delay is therefore followed immediately, which is
        the reading every crawler gives ``Crawl-delay`` and the only one that
        does not punish a slow host twice.
        """
        key = host_key(url)
        if key is None or delay <= 0:
            return 0.0
        with self._lock:
            now = self._clock()
            earliest = self._next.get(key, now)
            self._next[key] = max(now, earliest) + delay
            return max(0.0, earliest - now)

    def clear(self) -> None:
        """Forget every host, as if nothing had been asked yet."""
        with self._lock:
            self._next.clear()


class ThrottledFetcher:
    """A :class:`~maxicrawler.web.fetcher.PageFetcher` that waits its turn.

    Decorates another fetcher and changes nothing about what it returns or
    raises — only when it is called. Everything above therefore keeps working
    unchanged, including the parts that have opinions about timeouts and
    redirects.

    With no minimum and no delay source this is a no-op, which is the default
    MaxiCrawler ships: waiting a second between requests nobody asked us to
    space out is a cost with no beneficiary. A host that *does* ask, through
    ``Crawl-delay``, is obeyed.
    """

    def __init__(
        self,
        fetcher: PageFetcher,
        *,
        schedule: HostSchedule | None = None,
        minimum: float = 0.0,
        delay_for: DelaySource | None = None,
        waiter: Waiter | None = None,
    ) -> None:
        if minimum < 0:
            msg = "minimum must not be negative"
            raise ValueError(msg)
        self._fetcher = fetcher
        self._schedule = schedule if schedule is not None else HostSchedule()
        self._minimum = minimum
        self._delay_for = delay_for
        self._waiter = waiter if waiter is not None else sleep

    @property
    def schedule(self) -> HostSchedule:
        """Return the schedule this fetcher books its slots in."""
        return self._schedule

    def fetch(self, url: str) -> FetchedPage:
        """Wait until *url*'s host may be asked again, then fetch it."""
        wait = self._schedule.reserve(url, self._delay(url))
        if wait > 0:
            self._waiter(wait)
        return self._fetcher.fetch(url)

    def _delay(self, url: str) -> float:
        """Return how long this host must be left alone between requests.

        The larger of what the operator configured and what the host asked
        for: a site may ask us to be *slower* than we planned, never faster.
        """
        stated = self._delay_for(url) if self._delay_for is not None else None
        return max(self._minimum, stated or 0.0)
