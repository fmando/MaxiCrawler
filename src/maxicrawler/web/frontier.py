"""What to crawl next, and what has been crawled already.

Two collaborators that are deliberately not one. **The frontier decides order;
the visited set decides identity.** Mercator's crawler splits the same
responsibilities for the same reason — a queue that also owns identity cannot
later be swapped for a queue that owns only order, and priority scheduling,
per-host politeness and a distributed queue are all changes to order alone.

The visited key is **not** the discovery key, and the difference is not
cosmetic. :func:`~maxicrawler.utils.urls.normalize_url` preserves URL
fragments, because a legacy Mega share keeps its handle and decryption key
there (ADR-007). But ``page#intro`` and ``page#setup`` are one page to fetch.
A crawler that shared one key with the discovery pipeline would either fetch a
page once per anchor in its own navigation menu, or destroy every Mega link it
found. So discovery keeps the fragment and the frontier drops it.
"""

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from maxicrawler.utils import normalize_url, strip_fragment


@dataclass(frozen=True, slots=True)
class CrawlItem:
    """One page waiting to be fetched, and where it came from."""

    url: str
    depth: int = 0
    """Link distance from the seed; the seed itself is zero."""

    discovered_from: str | None = None
    """The page that linked to it; ``None`` for the seed."""


def visit_key(url: str) -> str:
    """Return the identity two URLs share when they are the same page.

    Scheme and host are lowercased, a default port is dropped and the query is
    sorted by :func:`~maxicrawler.utils.urls.normalize_url`; the fragment is
    then removed, because it addresses a position inside a page rather than a
    page.

    Raises:
        ValueError: *url* is not an absolute HTTP(S) URL.
    """
    return strip_fragment(normalize_url(url))


@runtime_checkable
class VisitedSet(Protocol):
    """Remembers which pages a crawl has already claimed."""

    def register(self, key: str) -> bool:
        """Record *key* and report whether it was new.

        "Known" means queued *or* fetched, deliberately: registering at the
        moment a URL is enqueued is what keeps the same page from sitting in
        the frontier fifty times because fifty pages link to it.
        """
        ...

    def __contains__(self, key: object) -> bool:
        """Return whether *key* has been registered."""
        ...

    def __len__(self) -> int:
        """Return how many keys are known."""
        ...


class InMemoryVisitedSet:
    """A :class:`VisitedSet` backed by a plain set.

    Enough for a crawl bounded by a page limit. A crawl that has to survive the
    process replaces this with an adapter over a table; nothing else changes,
    which is why this is a protocol at all.
    """

    __slots__ = ("_keys",)

    def __init__(self, keys: Iterable[str] = ()) -> None:
        self._keys: set[str] = set(keys)

    def register(self, key: str) -> bool:
        """Record *key* and report whether it was new."""
        if key in self._keys:
            return False
        self._keys.add(key)
        return True

    def __contains__(self, key: object) -> bool:
        """Return whether *key* has been registered."""
        return key in self._keys

    def __len__(self) -> int:
        """Return how many keys are known."""
        return len(self._keys)

    def __repr__(self) -> str:
        """Return a representation naming the size only."""
        return f"{type(self).__name__}(known={len(self._keys)})"


@runtime_checkable
class Frontier(Protocol):
    """Holds the pages a crawl still intends to fetch, in some order.

    Implementations decide **order and nothing else**. They do not deduplicate,
    they do not know about depth limits, and they do not know about scope: by
    the time an item arrives here the engine has already decided it should be
    fetched.
    """

    def push(self, item: CrawlItem) -> None:
        """Add *item* to the backlog."""
        ...

    def pop(self) -> CrawlItem | None:
        """Return the next item, or ``None`` when the backlog is empty."""
        ...

    @property
    def pending(self) -> int:
        """Return how many items are still waiting."""
        ...


class FifoFrontier:
    """A breadth-first frontier.

    First in, first out, which for a crawl means every page at depth *n* is
    fetched before any page at depth *n + 1*. That is the right default on
    merit rather than only by simplicity: a depth-limited crawl wants the
    shallow, closer-to-the-seed pages of a site, and breadth-first reaches them
    first.

    Items are handed out one at a time so that adding workers later requires no
    change here — the same property that lets several workers share
    :class:`~maxicrawler.downloader.DownloadQueue`.
    """

    __slots__ = ("_items",)

    def __init__(self, items: Iterable[CrawlItem] = ()) -> None:
        self._items: deque[CrawlItem] = deque(items)

    def push(self, item: CrawlItem) -> None:
        """Append *item* to the back of the queue."""
        self._items.append(item)

    def pop(self) -> CrawlItem | None:
        """Return the item at the front, or ``None`` when none is waiting.

        ``None`` means the backlog is empty. A future scheduler will need to
        distinguish that from *"nothing yet"* — a delay before a host may be
        contacted again — which is one added member on this protocol and one
        branch in the engine loop, not a redesign.
        """
        if not self._items:
            return None
        return self._items.popleft()

    @property
    def pending(self) -> int:
        """Return how many items are still waiting."""
        return len(self._items)

    def __len__(self) -> int:
        """Return how many items are still waiting."""
        return len(self._items)

    def __bool__(self) -> bool:
        """Return whether any item is still waiting."""
        return bool(self._items)

    def __repr__(self) -> str:
        """Return a representation naming the backlog size only."""
        return f"{type(self).__name__}(pending={len(self._items)})"
