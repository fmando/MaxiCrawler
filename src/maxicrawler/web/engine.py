"""The loop that turns one crawled page into many.

This is the whole of recursion, and it is deliberately a loop *above* the
crawler rather than a change inside it.
:class:`~maxicrawler.web.service.WebDiscoveryService` still answers exactly one
question — *"which URLs does this page contain?"* — and knows nothing about
frontiers, depth or scope. Everything recursive lives here, in about a hundred
lines, because the pieces it composes were built to be composed.

Three properties are worth naming before reading the code.

**There is one gate.** :meth:`CrawlEngine._consider` is the only place a URL is
turned away, and every turn-away is counted with its reason. A consequence
worth having: the frontier only ever holds URLs that will actually be fetched,
which is what bounds its size without needing a cap.

**One dead page never stops a run.** A failure on any page becomes a
:class:`~maxicrawler.web.report.PageOutcome` and the loop continues, the same
rule the download manager follows. The seed is the exception: a crawl whose
starting point cannot be read has nothing to report, so the caller is told by
an exception rather than handed an empty report.

**Every ending produces a report.** Running out of work, hitting the page
ceiling and being interrupted are three states, not one success and two
failures.
"""

from collections import Counter
from datetime import UTC, datetime
from time import monotonic

from maxicrawler.web.errors import CrawlError, PolicyRefusedError
from maxicrawler.web.frontier import (
    CrawlItem,
    FifoFrontier,
    Frontier,
    InMemoryVisitedSet,
    VisitedSet,
    visit_key,
)
from maxicrawler.web.models import CrawlResult
from maxicrawler.web.policy import AllowAllPolicy, CrawlPolicy
from maxicrawler.web.report import CrawlReport, CrawlStatistics, PageOutcome, SkipReason
from maxicrawler.web.service import WebDiscoveryService
from maxicrawler.web.session import CrawlControl, CrawlSession, CrawlState


class CrawlEngine:
    """Crawls from a seed, following links until a limit or the work runs out.

    Every collaborator is injected and every one of them is a protocol, so a
    priority frontier, a persistent visited set, or a policy that reads
    ``robots.txt`` each replace one argument and change nothing here.
    """

    def __init__(
        self,
        service: WebDiscoveryService,
        *,
        frontier: Frontier | None = None,
        visited: VisitedSet | None = None,
        policy: CrawlPolicy | None = None,
        control: CrawlControl | None = None,
    ) -> None:
        self._service = service
        self._frontier = frontier if frontier is not None else FifoFrontier()
        self._visited = visited if visited is not None else InMemoryVisitedSet()
        self._policy = policy if policy is not None else AllowAllPolicy()
        self._control = control if control is not None else CrawlControl()
        self._skips: Counter[SkipReason] = Counter()
        self._pages: list[PageOutcome] = []
        self._fetched: set[str] = set()
        self._deepest = 0
        self._max_depth = 0

    @property
    def control(self) -> CrawlControl:
        """Return the handle that reports state and requests a stop."""
        return self._control

    @property
    def frontier(self) -> Frontier:
        """Return the frontier this engine draws from."""
        return self._frontier

    @property
    def visited(self) -> VisitedSet:
        """Return the set of pages this engine has claimed."""
        return self._visited

    def run(self, session: CrawlSession) -> CrawlReport:
        """Crawl from the seed of *session* and report what happened.

        The crawl ends when the frontier runs dry (``COMPLETED``), when the
        page ceiling is reached (``PAGE_LIMIT``), or when a stop is requested
        by :meth:`~maxicrawler.web.session.CrawlControl.request_stop` or by
        Ctrl-C (``INTERRUPTED``). All three produce a report.

        Raises:
            CrawlError: the **seed** could not be read, or was refused before
                it was reached. A failure on any other page is recorded and the
                crawl carries on.
        """
        self._reset(session)
        started = monotonic()
        scan = session.scan_session
        self._control.state = CrawlState.RUNNING
        self._service.start(scan)
        try:
            self._seed(session)
            state = self._drain(session)
        except KeyboardInterrupt:
            # Ctrl-C is a stop, not a crash. Whatever was crawled is still
            # worth reporting, and the state says why it ended.
            state = CrawlState.INTERRUPTED
        except CrawlError:
            self._service.finish(scan)
            self._control.state = CrawlState.INTERRUPTED
            raise
        summary = self._service.finish(scan)
        self._control.state = state
        return CrawlReport(
            session=session,
            state=state,
            statistics=CrawlStatistics.of(
                pages_visited=sum(1 for page in self._pages if page.succeeded),
                pages_failed=sum(1 for page in self._pages if not page.succeeded),
                skips=self._skips,
                max_depth_reached=self._deepest,
                frontier_remaining=self._frontier.pending,
                elapsed_seconds=monotonic() - started,
            ),
            summary=summary,
            pages=tuple(self._pages),
            finished_at=datetime.now(UTC),
        )

    def _reset(self, session: CrawlSession) -> None:
        """Start a fresh set of counters for this run.

        Counters reset; **identity does not**. The visited set may have been
        supplied by the caller — seeded from an earlier crawl, or shared
        between several seeds — and clearing it would throw that away. So a
        second :meth:`run` on the same engine reports its own numbers while
        continuing to remember what has already been fetched, which is what
        makes crawling three seeds without revisiting a shared page work.
        """
        self._skips = Counter()
        self._pages = []
        self._deepest = 0
        self._max_depth = session.options.max_depth

    def _seed(self, session: CrawlSession) -> None:
        """Queue the starting point, or say why there is nothing to crawl."""
        self._consider(CrawlItem(url=session.seed_url, depth=0))
        if self._frontier.pending:
            return
        reason = next(iter(self._skips), SkipReason.UNUSABLE)
        msg = f"nothing to crawl: the seed was {reason}"
        raise PolicyRefusedError(msg)

    def _drain(self, session: CrawlSession) -> CrawlState:
        """Fetch pages until something stops the crawl, and say what did."""
        options = session.options
        while True:
            if self._control.stop_requested:
                return CrawlState.INTERRUPTED
            if len(self._pages) >= options.max_pages:
                return CrawlState.PAGE_LIMIT
            item = self._frontier.pop()
            if item is None:
                return CrawlState.COMPLETED
            if self._was_fetched(item.url):
                # A redirect reached this page under another URL after this
                # item had already been queued. Enqueue-time identity cannot
                # catch that, because the redirect was not known yet.
                self._skips[SkipReason.ALREADY_SEEN] += 1
                continue
            self._visit(item, session)

    def _visit(self, item: CrawlItem, session: CrawlSession) -> None:
        """Fetch one page, record what happened, and queue what it linked to."""
        self._deepest = max(self._deepest, item.depth)
        try:
            result = self._service.crawl_page(item.url, session.scan_session)
        except CrawlError as error:
            if not self._pages:
                raise
            self._pages.append(
                PageOutcome(
                    url=item.url,
                    depth=item.depth,
                    discovered_from=item.discovered_from,
                    error=str(error),
                )
            )
            return
        self._record(item, result)
        self._enqueue_links(item, result)

    def _record(self, item: CrawlItem, result: CrawlResult) -> None:
        """Note the page, and claim both URLs that led to it.

        Claiming the final URL as well as the requested one is what keeps a
        redirect from being fetched twice — once through a link to the
        redirector, and once through a link straight to its target. The
        requested URL is remembered too, so a *later* redirect that lands here
        is recognised at the moment it is popped.
        """
        self._claim(item.url)
        self._claim(result.final_url)
        self._pages.append(
            PageOutcome(
                url=item.url,
                depth=item.depth,
                final_url=result.final_url,
                status=result.page.status,
                discovered_from=item.discovered_from,
                title=result.document.title,
                canonical_url=result.document.canonical_url,
                link_count=result.link_count,
            )
        )

    def _enqueue_links(self, item: CrawlItem, result: CrawlResult) -> None:
        """Offer everything this page linked to for the next round."""
        depth = item.depth + 1
        for link in result.links:
            self._consider(
                CrawlItem(url=link.resolved_url, depth=depth, discovered_from=result.final_url)
            )

    def _consider(self, item: CrawlItem) -> None:
        """Queue *item*, or count why it will never be fetched.

        The single gate. Depth is checked first because it costs nothing and
        rejects the most; scope next; identity last — so a URL refused for
        being out of scope is *not* also remembered as seen, and a later crawl
        under a wider scope still finds it.

        The counters therefore count occurrences rather than distinct URLs: a
        link to the same off-site page from forty pages is forty skips, which
        is the honest answer to "how much did this crawl turn away".
        """
        if item.depth > self._max_depth:
            self._skips[SkipReason.TOO_DEEP] += 1
            return
        if not self._policy.may_fetch(item.url).allowed:
            self._skips[SkipReason.OUT_OF_SCOPE] += 1
            return
        try:
            key = visit_key(item.url)
        except ValueError:
            self._skips[SkipReason.UNUSABLE] += 1
            return
        if not self._visited.register(key):
            self._skips[SkipReason.ALREADY_SEEN] += 1
            return
        self._frontier.push(item)

    def _claim(self, url: str) -> None:
        """Mark *url* as both known and fetched, without queuing it."""
        try:
            key = visit_key(url)
        except ValueError:
            return
        self._visited.register(key)
        self._fetched.add(key)

    def _was_fetched(self, url: str) -> bool:
        """Return whether some earlier page in this crawl already answered here."""
        try:
            return visit_key(url) in self._fetched
        except ValueError:
            return False
