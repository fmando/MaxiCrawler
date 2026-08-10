"""The loop that turns one crawled page into many.

This is the whole of recursion, and it is deliberately a loop *above* the
crawler rather than a change inside it.
:class:`~maxicrawler.web.service.WebDiscoveryService` still answers exactly one
question — *"which URLs does this page contain?"* — and knows nothing about
frontiers, depth or scope. Everything recursive lives here, in about a hundred
lines, because the pieces it composes were built to be composed.

Three properties are worth naming before reading the code.

**There are two gates, and the difference between them is cost.**
:meth:`CrawlEngine._consider` runs when a URL is *found* and asks only policies
that answer from the URL itself. :meth:`CrawlEngine._admit` runs when a URL is
*popped*, immediately before the request, and is where a policy that has to
make a request of its own belongs — reading ``/robots.txt``, resolving a host.

The rule that decides which gate a policy goes to:

    A policy that can make a request is asked once, immediately before the
    request it guards. A policy that cannot is asked when the URL is found, so
    the frontier stays clean.

Asking robots.txt at the first gate would tie the number of ``/robots.txt``
requests to the number of *discovered* hosts rather than to anything the
operator set: one page linking to three hundred domains would cost three
hundred requests to then crawl fifty pages. At the second gate it is asked only
about URLs that are genuinely next.

Both gates count every turn-away with its reason, through the same
:func:`~maxicrawler.web.report.skip_reason_for`, so there is still exactly one
vocabulary for "why not".

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

from maxicrawler.events import (
    CrawlFinished,
    CrawlStarted,
    EventBus,
    PageCrawled,
    PageFailed,
)
from maxicrawler.web.errors import ContentTypeError, CrawlError, PolicyRefusedError
from maxicrawler.web.frontier import (
    CrawlItem,
    FifoFrontier,
    Frontier,
    InMemoryVisitedSet,
    VisitedSet,
    visit_key,
)
from maxicrawler.web.models import CrawlResult, LinkKind
from maxicrawler.web.policy import (
    AllowAllPolicy,
    CompositePolicy,
    CrawlPolicy,
    PolicyDecision,
    PolicyRule,
    SameDomainPolicy,
)
from maxicrawler.web.report import (
    CrawlReport,
    CrawlStatistics,
    PageOutcome,
    SkipReason,
    skip_reason_for,
)
from maxicrawler.web.repository import CrawlRepository, NullCrawlRepository
from maxicrawler.web.resolve import looks_like_a_page
from maxicrawler.web.service import WebDiscoveryService
from maxicrawler.web.session import CrawlControl, CrawlSession, CrawlState

FOLLOWABLE_KINDS = frozenset({LinkKind.ANCHOR, LinkKind.FRAME, LinkKind.REDIRECT, LinkKind.TEXT})
"""Which kinds of link could plausibly lead to another page.

A stylesheet, a script and an image are resources, not documents to walk. They
are discovered, classified and counted like every other URL — the crawler's job
is to *find* resources — but following one buys a round trip that ends in "this
is not a page", which the markup already said.

The four that remain are the ones a reader could follow: a link, a frame, a
meta refresh, and a URL somebody wrote out in the text.
"""


def _reason_of(refusal: PolicyRefusedError) -> SkipReason:
    """Return how a report counts a refusal that arrived as an exception."""
    return skip_reason_for(PolicyDecision.refuse(str(refusal), rule=refusal.rule))


class CrawlEngine:
    """Crawls from a seed, following links until a limit or the work runs out.

    Every collaborator is injected and every one of them is a protocol, so a
    priority frontier, a persistent visited set, or a policy that reads
    ``robots.txt`` each replace one argument and change nothing here.

    Two of those arguments are policies, and which one a policy belongs in is
    decided by whether it can make a request: *policy* is asked when a URL is
    found, *gate* immediately before it is fetched. See the module docstring.
    """

    def __init__(
        self,
        service: WebDiscoveryService,
        *,
        frontier: Frontier | None = None,
        visited: VisitedSet | None = None,
        policy: CrawlPolicy | None = None,
        gate: CrawlPolicy | None = None,
        control: CrawlControl | None = None,
        event_bus: EventBus | None = None,
        repository: CrawlRepository | None = None,
    ) -> None:
        self._service = service
        self._repository = repository if repository is not None else NullCrawlRepository()
        self._frontier = frontier if frontier is not None else FifoFrontier()
        self._visited = visited if visited is not None else InMemoryVisitedSet()
        self._policy = policy if policy is not None else AllowAllPolicy()
        self._gate = gate if gate is not None else AllowAllPolicy()
        self._control = control if control is not None else CrawlControl()
        self._event_bus = event_bus
        self._scope: CrawlPolicy = self._policy
        self._skips: Counter[SkipReason] = Counter()
        self._refusal: PolicyDecision | None = None
        self._kinds: Counter[LinkKind] = Counter()
        self._pages: list[PageOutcome] = []
        self._fetched: set[str] = set()
        self._attempts = 0
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
        self._repository.start_crawl(session)
        self._service.start(scan)
        self._publish(
            CrawlStarted(
                session_id=session.session_id,
                seed_url=session.seed_url,
                max_depth=session.options.max_depth,
            )
        )
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
        visited = sum(1 for page in self._pages if page.succeeded)
        failed = len(self._pages) - visited
        self._publish(
            CrawlFinished(
                session_id=session.session_id,
                state=str(state),
                pages_visited=visited,
                pages_failed=failed,
            )
        )
        report = CrawlReport(
            session=session,
            state=state,
            statistics=CrawlStatistics.of(
                pages_visited=visited,
                pages_failed=failed,
                pages_attempted=self._attempts,
                skips=self._skips,
                kinds=self._kinds,
                max_depth_reached=self._deepest,
                frontier_remaining=self._frontier.pending,
                elapsed_seconds=monotonic() - started,
            ),
            summary=summary,
            pages=tuple(self._pages),
            finished_at=datetime.now(UTC),
        )
        self._repository.finish_crawl(session, report)
        return report

    def _publish(self, event: CrawlStarted | CrawlFinished | PageCrawled | PageFailed) -> None:
        """Announce *event*, when anybody asked to be told.

        The bus is optional so a library caller that wants none of this pays
        nothing for it, and a future user interface subscribes rather than
        polls.
        """
        if self._event_bus is not None:
            self._event_bus.publish(event)

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
        self._kinds = Counter()
        self._refusal = None
        self._pages = []
        self._attempts = 0
        self._deepest = 0
        self._max_depth = session.options.max_depth
        self._scope = self._scope_for(session)

    def _scope_for(self, session: CrawlSession) -> CrawlPolicy:
        """Return the policy this crawl is actually held to.

        The engine derives the domain restriction from the session rather than
        leaving it to whoever wired the engine. An option that only takes
        effect when a caller separately injects a matching policy is a trap:
        the report and the database row would claim the crawl stayed on one
        host while it wandered. An injected policy still applies — it is asked
        alongside, and the first refusal wins.
        """
        if not session.options.same_domain:
            return self._policy
        scope = SameDomainPolicy(
            session.seed_url, include_subdomains=session.options.include_subdomains
        )
        return CompositePolicy([scope, self._policy])

    def _seed(self, session: CrawlSession) -> None:
        """Queue the starting point, or say why there is nothing to crawl."""
        self._consider(CrawlItem(url=session.seed_url, depth=0))
        if self._frontier.pending:
            return
        reason = next(iter(self._skips), SkipReason.UNUSABLE)
        msg = f"nothing to crawl: the seed was {reason}"
        # The rule travels with the error, not only the sentence: a caller
        # deciding what to show a person should not have to read English to
        # find out whether this was scope, robots.txt, or an address.
        rule = self._refusal.rule if self._refusal is not None else PolicyRule.SCOPE
        raise PolicyRefusedError(msg, rule=rule)

    def _drain(self, session: CrawlSession) -> CrawlState:
        """Fetch pages until something stops the crawl, and say what did."""
        options = session.options
        while True:
            if self._control.stop_requested:
                return CrawlState.INTERRUPTED
            if self._attempts >= options.max_pages:
                # Requests, not pages read. A site whose links all answer with
                # something other than a page must not be able to draw an
                # unbounded number of them.
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
            if not self._admit(item):
                continue
            self._visit(item, session)

    def _admit(self, item: CrawlItem) -> bool:
        """Ask the costly policies, immediately before the request they cost.

        Checked here rather than at :meth:`_consider` so that a policy which
        makes a request of its own is asked about URLs that are genuinely next,
        and about no others.

        Refused *before* the attempt is counted, so a URL that robots.txt
        forbids does not consume a page of the ceiling. It never became a
        request, and the ceiling counts requests.

        Raises:
            PolicyRefusedError: the **seed** was refused. Nothing has been read,
                so there is no report to hand back and the caller is told by an
                exception — the same rule a seed that cannot be fetched follows.
        """
        decision = self._gate.may_fetch(item.url)
        if decision.allowed:
            return True
        reason = skip_reason_for(decision)
        if not self._pages:
            msg = f"nothing to crawl: the seed was {reason}"
            raise PolicyRefusedError(msg, rule=decision.rule)
        self._skips[reason] += 1
        return False

    def _visit(self, item: CrawlItem, session: CrawlSession) -> None:
        """Fetch one page, record what happened, and queue what it linked to."""
        self._deepest = max(self._deepest, item.depth)
        self._attempts += 1
        try:
            result = self._service.crawl_page(item.url, session.scan_session)
        except PolicyRefusedError as refusal:
            # A rule said no *during* the fetch rather than before it — which
            # today means a redirect landed somewhere the destination was not
            # allowed to be. Still a skip rather than a failure: nothing broke,
            # we declined.
            if not self._pages:
                raise
            self._skips[_reason_of(refusal)] += 1
            return
        except ContentTypeError:
            # Not a failure. We asked "is this a page?" and got a clear no,
            # which is the same answer the extension filter gives for free —
            # this is the residue it cannot judge from a URL alone.
            if not self._pages:
                raise
            self._skips[SkipReason.NOT_A_PAGE] += 1
            return
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
            self._publish(
                PageFailed(
                    session_id=session.session_id,
                    url=item.url,
                    depth=item.depth,
                    reason=str(error),
                )
            )
            return
        self._record(item, result)
        self._publish(
            PageCrawled(
                session_id=session.session_id,
                url=item.url,
                final_url=result.final_url,
                depth=item.depth,
                status=result.page.status,
                link_count=result.link_count,
            )
        )
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
        """Offer everything this page linked to for the next round.

        Everything is counted and everything reached the discovery pipeline
        already; only what could plausibly *be* a page is offered to the
        frontier. Two filters, and both save a request rather than interpret an
        answer: the kind of link it was written as, and the extension its path
        ends in.

        This governs what the crawler picks up on its own. A URL the operator
        named on the command line is always attempted — an explicit instruction
        outranks a heuristic, and being told what came back beats being told it
        looked wrong.
        """
        depth = item.depth + 1
        for link in result.links:
            self._kinds[link.kind] += 1
            if link.kind not in FOLLOWABLE_KINDS or not looks_like_a_page(link.resolved_url):
                self._skips[SkipReason.NOT_A_PAGE] += 1
                continue
            self._consider(
                CrawlItem(url=link.resolved_url, depth=depth, discovered_from=result.final_url)
            )

    def _consider(self, item: CrawlItem) -> None:
        """Queue *item*, or count why it will never be fetched.

        The first gate, and the only one a URL passes before it is queued.
        Depth is checked first because it costs nothing and rejects the most;
        scope next; identity last — so a URL refused for being out of scope is
        *not* also remembered as seen, and a later crawl under a wider scope
        still finds it.

        The counters therefore count occurrences rather than distinct URLs: a
        link to the same off-site page from forty pages is forty skips, which
        is the honest answer to "how much did this crawl turn away".

        What a refusal is counted as comes from the decision itself rather than
        from this line, so a policy added later is counted under its own name
        without the gate learning what it does.
        """
        if item.depth > self._max_depth:
            self._skips[SkipReason.TOO_DEEP] += 1
            return
        decision = self._scope.may_fetch(item.url)
        if not decision.allowed:
            self._refusal = decision
            self._skips[skip_reason_for(decision)] += 1
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
