"""What a crawl did, once it is over.

The report composes :class:`~maxicrawler.crawler.DiscoverySummary` rather than
restating it, the same way
:class:`~maxicrawler.web.models.CrawlResult` does for one page. Crawl-level
counters — pages fetched, pages refused, how deep it got — sit *beside* the
discovery counters, never instead of them, so ``crawl`` and ``discover`` keep
reporting the same numbers in the same words.

The two views agree by construction rather than by addition:
``documents_processed`` on the discovery side is incremented once per fetched
page, so it always equals ``pages_visited`` on the crawl side.

:class:`PageOutcome` is built for every page because the report needs it. That
is also what makes per-page persistence an addition rather than a redesign: it
is one call inside the loop and one table, with the value already in hand.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from maxicrawler.crawler import DiscoverySummary
from maxicrawler.web.models import LinkKind
from maxicrawler.web.session import CrawlSession, CrawlState


class SkipReason(StrEnum):
    """Why a discovered URL was never fetched.

    Every URL turned away is counted under one of these. A report that said
    only how many pages it visited would leave a reader guessing why a crawl
    of a large site stopped at four pages; naming the reason answers it.
    """

    TOO_DEEP = "too deep"
    """Beyond ``--depth``. Discovered and classified, simply not followed."""

    OUT_OF_SCOPE = "out of scope"
    """Refused by a policy — a different domain, and later robots.txt."""

    ALREADY_SEEN = "already seen"
    """Queued or fetched earlier in this crawl."""

    NOT_A_PAGE = "not a page link"
    """A stylesheet, a script or an image — a resource, never a page.

    Still discovered, still classified, still counted. Just never fetched: a
    ``<link href>`` cannot answer with HTML, so following it costs a round trip
    to be told what the markup already said.
    """

    UNUSABLE = "unusable"
    """Not a URL this crawler can canonicalize, so not one it can track."""


@dataclass(frozen=True, slots=True)
class PageOutcome:
    """What happened to one page a crawl reached for."""

    url: str
    """The URL that was requested."""

    depth: int
    final_url: str | None = None
    """The URL that answered, after redirects; ``None`` when none did."""

    status: int | None = None
    discovered_from: str | None = None
    """The page that linked to it; ``None`` for the seed."""

    title: str | None = None
    canonical_url: str | None = None
    """What ``<link rel="canonical">`` claimed, recorded and never acted on.

    A page can declare a canonical it does not equal, and skipping a URL that
    was never fetched loses every outgoing link on it. Reported so a reader can
    see the claim; not used to decide anything.
    """

    link_count: int = 0
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        """Return whether the page was fetched and read."""
        return self.error is None

    @property
    def was_redirected(self) -> bool:
        """Return whether the answering URL differs from the requested one."""
        return self.final_url is not None and self.final_url != self.url


@dataclass(frozen=True, slots=True)
class CrawlStatistics:
    """Crawl-level counters, beside the discovery counters rather than over them."""

    pages_visited: int = 0
    """Pages fetched and read. Equals ``documents_processed``."""

    pages_failed: int = 0
    pages_attempted: int = 0
    """Requests the crawl actually issued, and what the page ceiling counts.

    Larger than ``pages_visited + pages_failed`` when a server answered with
    something that was not a page: that costs a request but is neither a page
    read nor a failure. The ceiling is measured against this so a site full of
    such links cannot draw an unbounded number of requests.
    """

    pages_skipped: int = 0
    skips_by_reason: tuple[tuple[SkipReason, int], ...] = ()
    links_by_kind: tuple[tuple[LinkKind, int], ...] = ()
    """Every link found, grouped by how it was written, in enum order."""

    max_depth_reached: int = 0
    frontier_remaining: int = 0
    """Pages still queued when the crawl stopped; non-zero after a limit."""

    elapsed_seconds: float = 0.0

    @property
    def requests_without_a_page(self) -> int:
        """Return how many requests answered with something that was not a page."""
        return max(0, self.pages_attempted - self.pages_visited - self.pages_failed)

    @classmethod
    def of(
        cls,
        *,
        pages_visited: int,
        pages_failed: int,
        pages_attempted: int,
        skips: Counter[SkipReason],
        kinds: Counter[LinkKind] | None = None,
        max_depth_reached: int,
        frontier_remaining: int,
        elapsed_seconds: float,
    ) -> "CrawlStatistics":
        """Return the counters for one crawl, ordering skips by frequency."""
        ordered = sorted(skips.items(), key=lambda entry: (-entry[1], str(entry[0])))
        counted = kinds or Counter()
        return cls(
            pages_visited=pages_visited,
            pages_failed=pages_failed,
            pages_attempted=pages_attempted,
            pages_skipped=sum(skips.values()),
            skips_by_reason=tuple(ordered),
            links_by_kind=tuple((kind, counted[kind]) for kind in LinkKind if counted[kind]),
            max_depth_reached=max_depth_reached,
            frontier_remaining=frontier_remaining,
            elapsed_seconds=elapsed_seconds,
        )


@dataclass(frozen=True, slots=True)
class CrawlReport:
    """Everything one crawl produced.

    Immutable and free of any terminal, so the same value serves the CLI
    renderer, a future JSON API and a future user interface.

    The report holds the session, and the session holds its
    :class:`~maxicrawler.web.session.RequestContext` — so a credential added
    there later *is* reachable from here by traversal. What must hold is
    narrower and enforceable: **nothing that serializes a report writes the
    context.** Neither the JSON renderer nor the repository reads it, and each
    of them asserts that where it lives rather than trusting this sentence.
    """

    session: CrawlSession
    state: CrawlState
    statistics: CrawlStatistics
    summary: DiscoverySummary
    pages: tuple[PageOutcome, ...]
    finished_at: datetime

    @property
    def seed_url(self) -> str:
        """Return the URL the crawl started from."""
        return self.session.seed_url

    @property
    def pages_visited(self) -> int:
        """Return how many pages were fetched and read."""
        return self.statistics.pages_visited

    @property
    def links_discovered(self) -> int:
        """Return every link found, duplicates included."""
        return self.summary.total_urls

    @property
    def failures(self) -> tuple[PageOutcome, ...]:
        """Return the pages that could not be read."""
        return tuple(page for page in self.pages if not page.succeeded)

    @property
    def was_complete(self) -> bool:
        """Return whether the crawl ran out of work rather than out of budget."""
        return self.state is CrawlState.COMPLETED
