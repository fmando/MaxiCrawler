"""Running a crawl, for whoever is asking.

The command line built this graph inline for as long as it was the only client.
It is extracted here unchanged so that a second client cannot end up with a
second, subtly different crawler — which is the failure this whole package
exists to prevent.

Two properties are worth stating before the code.

**A fresh graph per crawl.** :meth:`CrawlService.run` builds its own pipeline,
fetcher, parser, repositories and engine every time. That is what the CLI has
always done per invocation, and it is what makes two crawls at once safe
without touching :class:`~maxicrawler.crawler.DiscoveryPipeline`, which is not
thread-safe. Sharing a graph between crawls would be a bug; the shape of this
API is what makes that hard to do by accident.

**One event bus for both halves.** The pipeline publishes ``UrlDiscovered`` and
the engine publishes ``PageCrawled``; a caller that wants to watch a crawl needs
both on the same bus. The CLI passes none and sees nothing, which costs it
nothing.

**This is where responsible crawling is assembled**, and the only place. Each
piece is inert on its own — a robots policy nobody asks, a throttle with no
delay, a network guard nobody consults — and :meth:`CrawlService.build_engine`
is what makes them a crawl that obeys robots.txt, waits when a host asks it to,
and will not walk into the machine it is running on. Both clients get that by
going through here, which is why neither of them may build an engine itself.
"""

from datetime import UTC, datetime
from uuid import uuid4

from maxicrawler.config import Settings
from maxicrawler.crawler import (
    DiscoveryPipeline,
    DiscoveryRepository,
    NullDiscoveryRepository,
)
from maxicrawler.database import (
    SQLiteCrawlRepository,
    SQLiteDatabase,
    SQLiteDiscoveryRepository,
    StoredCrawl,
    StoredUrl,
)
from maxicrawler.events import EventBus
from maxicrawler.utils import require_http_scheme
from maxicrawler.web import HtmlLinkParser, UrllibPageFetcher, WebDiscoveryService
from maxicrawler.web.engine import CrawlEngine
from maxicrawler.web.fetcher import RedirectGuard
from maxicrawler.web.policy import CompositePolicy, CrawlPolicy
from maxicrawler.web.private import PrivateNetworkPolicy, redirect_guard
from maxicrawler.web.report import CrawlReport
from maxicrawler.web.repository import CrawlRepository, NullCrawlRepository
from maxicrawler.web.robots import MAX_ROBOTS_BYTES, ROBOTS_MEDIA_TYPES, RobotsPolicy
from maxicrawler.web.session import (
    CrawlControl,
    CrawlOptions,
    CrawlSession,
    RequestContext,
)
from maxicrawler.web.throttle import DelaySource, HostSchedule, ThrottledFetcher, Waiter


class CrawlService:
    """Everything a client needs to run a crawl, and nothing about showing it."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def settings(self) -> Settings:
        """Return the settings every crawl of this service is built from."""
        return self._settings

    def build_session(
        self,
        url: str,
        *,
        depth: int | None = None,
        max_pages: int | None = None,
        same_domain: bool | None = None,
        include_subdomains: bool = False,
        respect_robots: bool | None = None,
        scan_prose: bool = True,
        session_id: str | None = None,
    ) -> CrawlSession:
        """Return the session a crawl of *url* would run under.

        Every argument left as ``None`` takes its value from the configuration,
        so a client only states what its user actually chose. The identifier is
        minted here unless a caller supplies one.

        Raises:
            ValueError: *url* is not an absolute HTTP(S) URL, or an option is
                outside its range. The CLI turns this into a bad parameter and
                the web interface into a message beside the form; neither has
                to know the rules.
        """
        require_http_scheme(url)
        settings = self._settings
        options = CrawlOptions(
            max_depth=settings.crawl_depth if depth is None else depth,
            max_pages=settings.crawl_max_pages if max_pages is None else max_pages,
            same_domain=settings.crawl_same_domain if same_domain is None else same_domain,
            include_subdomains=include_subdomains,
            respect_robots=settings.respect_robots if respect_robots is None else respect_robots,
            scan_prose=scan_prose,
        )
        return CrawlSession(
            session_id=session_id or uuid4().hex,
            seed_url=url,
            started_at=datetime.now(UTC),
            options=options,
            context=RequestContext(
                user_agent=settings.user_agent, crawl_delay=settings.crawl_delay
            ),
        )

    def build_engine(
        self,
        session: CrawlSession,
        *,
        persist: bool = True,
        control: CrawlControl | None = None,
        event_bus: EventBus | None = None,
    ) -> CrawlEngine:
        """Return a crawl engine wired for *session*.

        Exposed beside :meth:`run` because a client that wants to hold the
        control — a Stop button, a test — needs the engine before it starts.

        The whole of this sprint's behaviour is assembled here, in an order
        that is not arbitrary:

        1.  one :class:`~maxicrawler.web.throttle.HostSchedule`, shared by every
            fetcher below, so a crawl's politeness is not divided by the number
            of fetchers it happens to be built from;
        2.  the private-network guard, in two forms — a pure one for the first
            gate and a resolving one for the second and for every redirect hop;
        3.  the robots policy over a fetcher of its own, because ``robots.txt``
            is ``text/plain`` under a limit of its own and a page is not;
        4.  the page fetcher, throttled, asking the robots policy how long this
            host wants to be left alone.

        The waiter is the crawl's own control when it has one, so a stop during
        a delay returns at once rather than holding a shutdown open.
        """
        bus = event_bus if event_bus is not None else EventBus()
        settings = self._settings
        schedule = HostSchedule()
        waiter = control.wait if control is not None else None
        guard = redirect_guard(self._private_policy(resolve=True))
        robots = self._robots_policy(session, schedule=schedule, waiter=waiter, guard=guard)
        service = WebDiscoveryService(
            DiscoveryPipeline(bus),
            fetcher=ThrottledFetcher(
                UrllibPageFetcher(
                    user_agent=session.context.user_agent,
                    timeout=settings.network_timeout,
                    max_response_bytes=settings.max_page_bytes,
                    max_redirects=settings.max_redirects,
                    guard=guard,
                ),
                schedule=schedule,
                minimum=session.context.crawl_delay,
                delay_for=self._delay_source(robots),
                waiter=waiter,
            ),
            parser=HtmlLinkParser(max_links=settings.max_links),
            repository=self._discovery_repository(persist=persist),
            scan_prose=session.options.scan_prose,
        )
        return CrawlEngine(
            service,
            policy=self._private_policy(resolve=False),
            gate=self._gate(robots),
            control=control,
            event_bus=bus,
            repository=self._crawl_repository(persist=persist),
        )

    def _gate(self, robots: RobotsPolicy | None) -> CrawlPolicy:
        """Return what is asked immediately before each request.

        The private-network rule first: it answers from a cache or a resolver
        and is the one refusal that must not depend on a stranger's server
        answering. robots.txt second, so a URL already refused never costs the
        request that would have read its ``/robots.txt``.
        """
        policies: list[CrawlPolicy] = [self._private_policy(resolve=True)]
        if robots is not None:
            policies.append(robots)
        return CompositePolicy(policies)

    def _robots_policy(
        self,
        session: CrawlSession,
        *,
        schedule: HostSchedule,
        waiter: Waiter | None,
        guard: RedirectGuard,
    ) -> RobotsPolicy | None:
        """Return the robots policy for *session*, or ``None`` when it opted out.

        Its fetcher shares the schedule but has no delay source of its own —
        the delay for a host is stated in the very file being fetched, so
        asking for it here would be a loop. Sharing the schedule is what keeps
        the request polite anyway.
        """
        if not session.options.respect_robots:
            return None
        settings = self._settings
        fetcher = ThrottledFetcher(
            UrllibPageFetcher(
                user_agent=session.context.user_agent,
                timeout=settings.robots_timeout,
                max_response_bytes=MAX_ROBOTS_BYTES,
                max_redirects=settings.max_redirects,
                accept=ROBOTS_MEDIA_TYPES,
                guard=guard,
            ),
            schedule=schedule,
            minimum=session.context.crawl_delay,
            waiter=waiter,
        )
        return RobotsPolicy(
            fetcher,
            user_agent=settings.robots_user_agent or session.context.user_agent,
            deny_on_error=settings.robots_deny_on_error,
            max_delay=settings.max_crawl_delay,
        )

    def _delay_source(self, robots: RobotsPolicy | None) -> DelaySource | None:
        """Return where a host's own ``Crawl-delay`` is read from, if anywhere."""
        if robots is None or not self._settings.respect_crawl_delay:
            return None
        return robots.delay_for

    def _private_policy(self, *, resolve: bool) -> PrivateNetworkPolicy:
        """Return the network guard, resolving names or reading them only.

        Built fresh rather than shared, because the two forms answer different
        questions and a cache of one is not a cache of the other. Built at all
        even when private addresses are allowed: a cloud metadata service stays
        refused either way.
        """
        settings = self._settings
        return PrivateNetworkPolicy(
            allow=settings.private_network_allowlist,
            allow_private=settings.allow_private_networks,
            resolve=resolve,
        )

    def run(
        self,
        session: CrawlSession,
        *,
        persist: bool = True,
        control: CrawlControl | None = None,
        event_bus: EventBus | None = None,
    ) -> CrawlReport:
        """Crawl *session* and return what it found.

        Raises:
            CrawlError: the seed could not be read, or was refused. Every other
                page failure is recorded in the report.
        """
        engine = self.build_engine(session, persist=persist, control=control, event_bus=event_bus)
        return engine.run(session)

    def _discovery_repository(self, *, persist: bool) -> DiscoveryRepository:
        """Return where discovered URLs should be written."""
        if not persist:
            return NullDiscoveryRepository()
        repository = SQLiteDiscoveryRepository(SQLiteDatabase(self._settings.database_path))
        repository.initialize()
        return repository

    def _crawl_repository(self, *, persist: bool) -> CrawlRepository:
        """Return where the crawl summary should be written."""
        if not persist:
            return NullCrawlRepository()
        repository = SQLiteCrawlRepository(SQLiteDatabase(self._settings.database_path))
        repository.initialize()
        return repository

    def stored_crawls(self, limit: int | None = 20) -> tuple[StoredCrawl, ...]:
        """Return the crawls this installation has recorded, newest first.

        Reading history needs no engine and no crawl, so it belongs here rather
        than making every client open a database of its own. ``None`` returns
        all of them, which is what a page showing the whole history asks for.
        """
        repository = SQLiteCrawlRepository(SQLiteDatabase(self._settings.database_path))
        repository.initialize()
        return repository.stored_crawls()[:limit]

    def stored_crawl(self, session_id: str) -> StoredCrawl | None:
        """Return the recorded crawl called *session_id*, if there is one.

        What makes a crawl from an earlier run of this program findable at all.
        A running process knows only the crawls it started; the database is
        what outlives it.
        """
        repository = SQLiteCrawlRepository(SQLiteDatabase(self._settings.database_path))
        repository.initialize()
        return repository.stored_crawl(session_id)

    def discovered_urls(self, session_id: str) -> tuple[StoredUrl, ...]:
        """Return the URLs one crawl recorded, in the order it found them.

        Empty for a crawl run with ``persist=False``, which is not the same
        thing as a crawl that found nothing — a caller that shows this has the
        report's own count to tell the two apart, and should.

        Every row is read rather than a page of them. The crawl's own page
        ceiling bounds how many there can be, and reading all of them is what
        lets a caller order them by something other than the insertion order
        the ``LIMIT`` would have to follow.
        """
        repository = SQLiteDiscoveryRepository(SQLiteDatabase(self._settings.database_path))
        repository.initialize()
        return repository.stored_urls(session_id)
