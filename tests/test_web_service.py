"""Tests for the web discovery service, end to end against a local server."""

from datetime import UTC, datetime

import pytest
from web_server import Site, serve

from maxicrawler.crawler import DiscoveryPipeline, DiscoveryRepository
from maxicrawler.domain import DiscoveryResult, ScanSession, Statistics
from maxicrawler.events import EventBus, ScanFinished, ScanStarted, UrlDiscovered
from maxicrawler.web import (
    ContentTypeError,
    CrawlResult,
    HttpStatusError,
    LinkKind,
    PolicyDecision,
    PolicyRefusedError,
    UrllibPageFetcher,
    WebDiscoveryService,
)

MEGA_LINK = "https://mega.nz/file/AaBbCcDd#0123456789abcdefghijklmnopqrstuvwxyzABC"


class RecordingRepository:
    """A :class:`DiscoveryRepository` that keeps what it was given."""

    def __init__(self) -> None:
        self.sessions: list[ScanSession] = []
        self.results: list[DiscoveryResult] = []
        self.finished: list[Statistics] = []

    def start_session(self, session: ScanSession) -> None:
        self.sessions.append(session)

    def save_result(self, session: ScanSession, result: DiscoveryResult) -> None:
        self.results.append(result)

    def finish_session(self, session: ScanSession, statistics: Statistics) -> None:
        self.finished.append(statistics)


class RefusingPolicy:
    """Refuses everything, so the seam can be observed."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def may_fetch(self, url: str) -> PolicyDecision:
        self.asked.append(url)
        return PolicyDecision.refuse("outside the crawl scope")


def make_session() -> ScanSession:
    """Return a fresh scan session."""
    return ScanSession(session_id="crawl-1", started_at=datetime.now(UTC))


def make_service(**kwargs: object) -> WebDiscoveryService:
    """Return a service wired to a real fetcher with a short timeout."""
    options: dict[str, object] = {
        "fetcher": UrllibPageFetcher(user_agent="MaxiCrawler/test", timeout=5.0)
    }
    options.update(kwargs)
    pipeline = options.pop("pipeline", None) or DiscoveryPipeline(EventBus())
    return WebDiscoveryService(pipeline, **options)  # type: ignore[arg-type]


PAGE = """
<html>
  <head>
    <title>Links</title>
    <link rel="stylesheet" href="/style.css">
  </head>
  <body>
    <a href="/one">one</a>
    <a href="two.html">two</a>
    <a href="https://elsewhere.test/three">three</a>
    <img src="/pic.png">
    <script src="/app.js"></script>
    <a href="mailto:someone@example.test">mail</a>
    <a href="#top">top</a>
  </body>
</html>
"""


def test_a_crawl_returns_the_page_the_document_and_the_summary() -> None:
    site = Site()
    site.add_html("/", PAGE)

    with serve(site) as base:
        result = make_service().crawl(f"{base}/", make_session())

    assert isinstance(result, CrawlResult)
    assert result.page.status == 200
    assert result.document.title == "Links"
    assert result.summary.documents_processed == 1


def test_every_markup_link_is_resolved_to_an_absolute_url() -> None:
    site = Site()
    site.add_html("/docs/index.html", PAGE)

    with serve(site) as base:
        result = make_service().crawl(f"{base}/docs/index.html", make_session())

    resolved = {link.resolved_url for link in result.links}
    assert f"{base}/one" in resolved
    assert f"{base}/docs/two.html" in resolved
    assert "https://elsewhere.test/three" in resolved


def test_links_are_counted_by_kind() -> None:
    site = Site()
    site.add_html("/", PAGE)

    with serve(site) as base:
        result = make_service().crawl(f"{base}/", make_session())

    counts = result.links_by_kind()
    assert counts[LinkKind.ANCHOR] == 3
    assert counts[LinkKind.IMAGE] == 1
    assert counts[LinkKind.SCRIPT] == 1
    assert counts[LinkKind.STYLESHEET] == 1


def test_references_the_pipeline_cannot_take_are_counted_as_skipped() -> None:
    site = Site()
    site.add_html("/", PAGE)

    with serve(site) as base:
        result = make_service().crawl(f"{base}/", make_session())

    assert result.skipped_links == 2  # the mailto: and the #top


# --- the discovery pipeline is not bypassed ----------------------------------


def test_the_statistics_come_from_the_shared_pipeline() -> None:
    site = Site()
    site.add_html("/", '<a href="/a">a</a><a href="/b">b</a><a href="/a">again</a>')

    with serve(site) as base:
        result = make_service().crawl(f"{base}/", make_session())

    assert result.summary.total_urls == 3
    assert result.summary.unique_urls == 2
    assert result.summary.duplicates_removed == 1


def test_a_mega_link_on_a_page_is_classified_by_the_mega_plugin() -> None:
    site = Site()
    site.add_html("/", f'<a href="{MEGA_LINK}">share</a><a href="/plain">plain</a>')

    with serve(site) as base:
        result = make_service().crawl(f"{base}/", make_session())

    usage = {entry.name: entry.count for entry in result.summary.plugin_usage}
    assert usage["mega"] == 1
    assert usage["generic"] == 1


def test_a_mega_link_keeps_its_key_through_the_whole_crawl() -> None:
    site = Site()
    site.add_html("/", f'<a href="{MEGA_LINK}">share</a>')

    with serve(site) as base:
        result = make_service().crawl(f"{base}/", make_session())

    assert any(link.resolved_url == MEGA_LINK for link in result.links)


def test_every_discovered_url_records_the_page_it_came_from() -> None:
    site = Site()
    site.add_html("/start", '<a href="/a">a</a>')
    repository = RecordingRepository()

    with serve(site) as base:
        make_service(repository=repository).crawl(f"{base}/start", make_session())

    assert repository.results[0].record.source_url == f"{base}/start"


def test_the_source_url_is_the_page_that_answered_not_the_one_requested() -> None:
    site = Site()
    site.add("/old", status=302, location="/new", body=b"", content_type=None)
    site.add_html("/new", '<a href="/a">a</a>')
    repository = RecordingRepository()

    with serve(site) as base:
        make_service(repository=repository).crawl(f"{base}/old", make_session())

    assert repository.results[0].record.source_url == f"{base}/new"


def test_the_repository_sees_the_whole_session() -> None:
    site = Site()
    site.add_html("/", '<a href="/a">a</a><a href="/b">b</a>')
    repository = RecordingRepository()

    with serve(site) as base:
        make_service(repository=repository).crawl(f"{base}/", make_session())

    assert len(repository.sessions) == 1
    assert len(repository.results) == 2
    assert len(repository.finished) == 1


def test_the_repository_satisfies_the_protocol() -> None:
    assert isinstance(RecordingRepository(), DiscoveryRepository)


def test_duplicates_are_not_persisted_twice() -> None:
    site = Site()
    site.add_html("/", '<a href="/a">a</a><a href="/a">a again</a>')
    repository = RecordingRepository()

    with serve(site) as base:
        make_service(repository=repository).crawl(f"{base}/", make_session())

    assert len(repository.results) == 1


def test_the_session_events_are_published() -> None:
    site = Site()
    site.add_html("/", '<a href="/a">a</a>')
    bus = EventBus()
    seen: list[object] = []
    for event_type in (ScanStarted, UrlDiscovered, ScanFinished):
        bus.subscribe(event_type, seen.append)

    with serve(site) as base:
        make_service(pipeline=DiscoveryPipeline(bus)).crawl(f"{base}/", make_session())

    assert [type(event) for event in seen] == [ScanStarted, UrlDiscovered, ScanFinished]


# --- redirects and both URLs -------------------------------------------------


def test_both_urls_survive_a_redirect() -> None:
    site = Site()
    site.add("/old", status=302, location="/new", body=b"", content_type=None)
    site.add_html("/new", "<html></html>")

    with serve(site) as base:
        result = make_service().crawl(f"{base}/old", make_session())

    assert result.requested_url == f"{base}/old"
    assert result.final_url == f"{base}/new"
    assert result.was_redirected is True


def test_relative_links_resolve_against_the_page_that_answered() -> None:
    site = Site()
    site.add("/old/page", status=302, location="/new/page", body=b"", content_type=None)
    site.add_html("/new/page", '<a href="sibling.html">x</a>')

    with serve(site) as base:
        result = make_service().crawl(f"{base}/old/page", make_session())

    assert result.links[0].resolved_url == f"{base}/new/sibling.html"


# --- base URLs and encodings -------------------------------------------------


def test_a_declared_base_governs_resolution() -> None:
    site = Site()
    site.add_html("/docs/", '<base href="https://cdn.test/a/"><a href="b.html">x</a>')

    with serve(site) as base:
        result = make_service().crawl(f"{base}/docs/", make_session())

    assert result.document.base_url == "https://cdn.test/a/"
    assert result.links[0].resolved_url == "https://cdn.test/a/b.html"


def test_a_declared_encoding_is_honoured() -> None:
    site = Site()
    site.add(
        "/",
        body="<html><title>Käse</title></html>".encode("iso-8859-1"),
        content_type="text/html; charset=iso-8859-1",
    )

    with serve(site) as base:
        result = make_service().crawl(f"{base}/", make_session())

    assert result.document.title == "Käse"
    assert result.page.encoding == "iso8859-1"


# --- prose scanning ----------------------------------------------------------


def test_a_bare_url_in_the_prose_is_discovered() -> None:
    site = Site()
    site.add_html("/", f"<p>Grab it here: {MEGA_LINK} enjoy</p>")

    with serve(site) as base:
        result = make_service().crawl(f"{base}/", make_session())

    prose = [link for link in result.links if link.kind is LinkKind.TEXT]
    assert prose[0].resolved_url == MEGA_LINK


def test_prose_scanning_can_be_turned_off() -> None:
    site = Site()
    site.add_html("/", f"<p>{MEGA_LINK}</p>")

    with serve(site) as base:
        result = make_service(scan_prose=False).crawl(f"{base}/", make_session())

    assert result.links == ()


def test_a_url_inside_a_script_is_not_taken_from_the_prose() -> None:
    site = Site()
    site.add_html("/", "<script>var u = 'https://evil.test/x';</script><p>ok</p>")

    with serve(site) as base:
        result = make_service().crawl(f"{base}/", make_session())

    assert all("evil.test" not in link.resolved_url for link in result.links)


# --- the policy seam ---------------------------------------------------------


def test_a_refused_url_is_never_fetched() -> None:
    site = Site()
    site.add_html("/", "<html></html>")
    policy = RefusingPolicy()

    with serve(site) as base:
        with pytest.raises(PolicyRefusedError, match="outside the crawl scope"):
            make_service(policy=policy).crawl(f"{base}/", make_session())

        assert site.requests == []
        assert policy.asked == [f"{base}/"]


# --- failures ----------------------------------------------------------------


def test_a_missing_page_is_reported_as_a_status_failure() -> None:
    site = Site()

    with serve(site) as base, pytest.raises(HttpStatusError):
        make_service().crawl(f"{base}/nope", make_session())


def test_a_non_html_response_is_reported() -> None:
    site = Site()
    site.add("/data.json", body=b"{}", content_type="application/json")

    with serve(site) as base, pytest.raises(ContentTypeError):
        make_service().crawl(f"{base}/data.json", make_session())


def test_a_page_with_no_links_yields_an_empty_but_valid_result() -> None:
    site = Site()
    site.add_html("/", "<html><body>nothing here</body></html>")

    with serve(site) as base:
        result = make_service().crawl(f"{base}/", make_session())

    assert result.links == ()
    assert result.link_count == 0
    assert result.summary.documents_processed == 1
    assert result.summary.plugin_usage == ()


def test_malformed_markup_still_yields_its_links() -> None:
    site = Site()
    site.add_html("/", "<p><div><a href='/a'>broken</p></a></div><a href='/b'")

    with serve(site) as base:
        result = make_service().crawl(f"{base}/", make_session())

    assert any(link.resolved_url.endswith("/a") for link in result.links)
