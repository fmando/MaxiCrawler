"""The persistence port used by the crawl engine.

Declared here, next to its consumer, rather than in the ``database`` package —
the same inversion :class:`~maxicrawler.crawler.DiscoveryRepository` already
uses. The engine depends on an abstraction it owns; adapters satisfy the
protocol structurally and are wired in by the composition root.

**What is stored, and what is not.** A crawl stores its *summary*: what it was
told to do, how it ended, and its counters. It does not store a row per page.
The URLs it discovered are already persisted, per URL, through the discovery
repository — "do not persist every page" means page outcomes, not links.

Adding page outcomes later is an addition rather than a redesign, because
:class:`~maxicrawler.web.report.PageOutcome` is built for every page anyway:
the report needs it. It is one ``save_page`` member on this protocol, one call
inside the engine loop, and one table.

**What must never be stored.** :class:`~maxicrawler.web.session.RequestContext`
is reachable from a report by traversal, and an implementation of this protocol
is exactly where a credential would leak into a file. No adapter reads it, and
:mod:`tests.test_crawl_repository` asserts that rather than trusting it.
"""

from typing import Protocol, runtime_checkable

from maxicrawler.web.report import CrawlReport
from maxicrawler.web.session import CrawlSession


@runtime_checkable
class CrawlRepository(Protocol):
    """Persists the outcome of a crawl.

    Implementations must be safe to call in the order ``start_crawl`` then
    ``finish_crawl``, and must tolerate a crawl that never finishes — a process
    killed mid-run leaves a started row, which is the honest record of what
    happened.
    """

    def start_crawl(self, session: CrawlSession) -> None:
        """Record the beginning of *session*."""
        ...

    def finish_crawl(self, session: CrawlSession, report: CrawlReport) -> None:
        """Record how *session* ended, together with its counters."""
        ...


class NullCrawlRepository:
    """A repository that discards everything.

    The default, so the engine stays usable and testable without a database.
    """

    def start_crawl(self, session: CrawlSession) -> None:
        """Do nothing."""

    def finish_crawl(self, session: CrawlSession, report: CrawlReport) -> None:
        """Do nothing."""
