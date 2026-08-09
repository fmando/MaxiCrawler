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
)
from maxicrawler.events import EventBus
from maxicrawler.utils import require_http_scheme
from maxicrawler.web import HtmlLinkParser, UrllibPageFetcher, WebDiscoveryService
from maxicrawler.web.engine import CrawlEngine
from maxicrawler.web.report import CrawlReport
from maxicrawler.web.repository import CrawlRepository, NullCrawlRepository
from maxicrawler.web.session import (
    CrawlControl,
    CrawlOptions,
    CrawlSession,
    RequestContext,
)


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
            scan_prose=scan_prose,
        )
        return CrawlSession(
            session_id=session_id or uuid4().hex,
            seed_url=url,
            started_at=datetime.now(UTC),
            options=options,
            context=RequestContext(user_agent=settings.user_agent),
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
        """
        bus = event_bus if event_bus is not None else EventBus()
        service = WebDiscoveryService(
            DiscoveryPipeline(bus),
            fetcher=UrllibPageFetcher(
                user_agent=session.context.user_agent,
                timeout=self._settings.network_timeout,
                max_response_bytes=self._settings.max_page_bytes,
                max_redirects=self._settings.max_redirects,
            ),
            parser=HtmlLinkParser(max_links=self._settings.max_links),
            repository=self._discovery_repository(persist=persist),
            scan_prose=session.options.scan_prose,
        )
        return CrawlEngine(
            service,
            control=control,
            event_bus=bus,
            repository=self._crawl_repository(persist=persist),
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

    def stored_crawls(self, limit: int = 20) -> tuple[StoredCrawl, ...]:
        """Return the crawls this installation has recorded, newest first.

        Reading history needs no engine and no crawl, so it belongs here rather
        than making every client open a database of its own.
        """
        repository = SQLiteCrawlRepository(SQLiteDatabase(self._settings.database_path))
        repository.initialize()
        return repository.stored_crawls()[:limit]
