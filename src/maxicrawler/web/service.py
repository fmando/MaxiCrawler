"""The workflow that turns one URL into the URLs its page contains.

The service is pure orchestration, deliberately the same shape as
:class:`~maxicrawler.crawler.LocalDiscoveryService`: the fetcher retrieves, the
decoder decodes, the parser reads, the resolver resolves, and
:class:`~maxicrawler.crawler.DiscoveryPipeline` does everything else — the
normalization, the duplicate detection, and the plugin resolution.

The pipeline is never bypassed. A URL found on a web page is classified by
exactly the same plugins, in exactly the same order, as one found in a local
Markdown file, and one fetched page counts as one processed document. That is
what makes ``crawl`` and ``discover`` report comparable numbers rather than two
different ideas of what a URL is.

It fetches exactly one page and holds no state about which page to visit next.
Recursion is therefore a matter of who calls this, in what order — see
:mod:`maxicrawler.web.policy` for the seam the scope rules plug into.
"""

from collections import Counter

from maxicrawler.crawler import (
    DiscoveryPipeline,
    DiscoveryRepository,
    DiscoverySummary,
    NullDiscoveryRepository,
    to_plugin_usage,
)
from maxicrawler.domain import ScanSession
from maxicrawler.extractors import scan_text
from maxicrawler.web.encoding import decode_body
from maxicrawler.web.errors import PolicyRefusedError
from maxicrawler.web.fetcher import PageFetcher
from maxicrawler.web.models import CrawlResult, LinkKind, PageInfo, PageLink, RawLink
from maxicrawler.web.parser import HtmlLinkParser, HtmlParser
from maxicrawler.web.policy import AllowAllPolicy, CrawlPolicy
from maxicrawler.web.resolve import resolve_links


class WebDiscoveryService:
    """Fetches one page and discovers the URLs it contains.

    Every collaborator is injected and every one of them is a protocol, so a
    test drives a full crawl without a socket and a future scheduler wraps the
    fetcher without this class knowing.
    """

    def __init__(
        self,
        pipeline: DiscoveryPipeline,
        *,
        fetcher: PageFetcher,
        parser: HtmlParser | None = None,
        policy: CrawlPolicy | None = None,
        repository: DiscoveryRepository | None = None,
        scan_prose: bool = True,
    ) -> None:
        self._pipeline = pipeline
        self._fetcher = fetcher
        self._parser = parser if parser is not None else HtmlLinkParser()
        self._policy = policy if policy is not None else AllowAllPolicy()
        self._repository = repository if repository is not None else NullDiscoveryRepository()
        self._scan_prose = scan_prose
        self._usage: Counter[str] = Counter()

    @property
    def pipeline(self) -> DiscoveryPipeline:
        """Return the pipeline this service feeds."""
        return self._pipeline

    @property
    def policy(self) -> CrawlPolicy:
        """Return the policy consulted before a page is fetched."""
        return self._policy

    def start(self, session: ScanSession) -> None:
        """Open *session*, resetting the tally a summary is built from.

        A caller that crawls several pages calls this once, then
        :meth:`crawl_page` per page, then :meth:`finish`. One crawl is one
        discovery session however many pages it turns out to hold.
        """
        self._usage = Counter()
        self._repository.start_session(session)
        self._pipeline.start(session)

    def finish(self, session: ScanSession) -> DiscoverySummary:
        """Close *session* and return what it discovered in total."""
        statistics = self._pipeline.finish(session)
        self._repository.finish_session(session, statistics)
        return DiscoverySummary(
            session=session,
            statistics=statistics,
            plugin_usage=to_plugin_usage(self._usage),
        )

    def crawl(self, url: str, session: ScanSession) -> CrawlResult:
        """Fetch *url* in a session of its own and report what it contains.

        A whole discovery session for a single page — which is what the
        one-page workflow wants and what a recursive crawl must not do fifty
        times over. :class:`~maxicrawler.web.engine.CrawlEngine` opens the
        session itself and calls :meth:`crawl_page` instead.

        Raises:
            PolicyRefusedError: the policy refused this URL.
            FetchError: the page could not be retrieved, or what came back was
                not a page.
        """
        self.start(session)
        result = self.crawl_page(url, session)
        self.finish(session)
        return result

    def crawl_page(self, url: str, session: ScanSession) -> CrawlResult:
        """Fetch one page and discover the URLs it contains.

        The whole of the crawler's work — fetch, decode, parse, resolve,
        discover — and none of the session bookkeeping around it. Statistics
        accumulate across every call between :meth:`start` and :meth:`finish`,
        so the summary on the result always describes the session so far
        rather than this page alone.

        Raises:
            PolicyRefusedError: the policy refused this URL. The caller named
                it explicitly, so refusing is a failure of the request; a
                recursive crawl catches this per URL and records a skipped
                page rather than ending the run.
            FetchError: the page could not be retrieved, or what came back was
                not a page.
        """
        decision = self._policy.may_fetch(url)
        if not decision.allowed:
            message = f"refused by the crawl policy: {decision.reason or 'no reason given'}"
            raise PolicyRefusedError(message, rule=decision.rule)
        page = self._fetcher.fetch(url)
        text, encoding = decode_body(page.body, declared=page.declared_charset)
        parsed = self._parser.parse(text)
        document = resolve_links(
            parsed,
            page_url=page.final_url,
            encoding=encoding,
            extra=self._prose_links(parsed.text),
        )
        summary = self._discover(document.links, session, source_url=page.final_url)
        return CrawlResult(
            page=PageInfo.of(page, encoding=encoding, size=len(page.body)),
            document=document,
            summary=summary,
        )

    def _prose_links(self, text: str) -> tuple[RawLink, ...]:
        """Return the URLs written as prose rather than as markup.

        Found with the rule :mod:`maxicrawler.extractors` already applies to
        plain text and Markdown, because a share link on a page is usually
        written out rather than linked, and two scanners would eventually
        disagree about what one looks like.
        """
        if not self._scan_prose:
            return ()
        return tuple(RawLink(found, LinkKind.TEXT, "", "") for found in scan_text(text))

    def _discover(
        self, links: tuple[PageLink, ...], session: ScanSession, *, source_url: str
    ) -> DiscoverySummary:
        """Run every link of one page through the pipeline.

        The tally lives on the service rather than in this method, because a
        crawl of forty pages needs one plugin count, not forty.
        """
        self._pipeline.record_document()
        for link in links:
            result = self._pipeline.discover(link.resolved_url, source_url=source_url)
            if result.is_duplicate:
                continue
            self._repository.save_result(session, result)
            resolution = result.resolution
            if resolution is not None and resolution.plugin is not None:
                self._usage[resolution.plugin.name] += 1
        return DiscoverySummary(
            session=session,
            statistics=self._pipeline.statistics,
            plugin_usage=to_plugin_usage(self._usage),
        )
