"""A guard against the suite ever leaving this machine.

The web crawler follows links onto other hosts by default, which is what makes
it useful for finding share links -- and what makes a test fixture holding a
real URL quietly turn the suite into a client of somebody else's server. That
happened once while Sprint 9 was being written, and this is the tripwire that
would have caught it.

Nothing here is clever: it opens a socket-level hole in the way a crawl would
and asserts that no test ever walks through it.
"""

import socket
from collections.abc import Iterator

import pytest
from web_server import Site, serve

from maxicrawler.crawler import DiscoveryPipeline
from maxicrawler.events import EventBus
from maxicrawler.web import UrllibPageFetcher, WebDiscoveryService
from maxicrawler.web.engine import CrawlEngine

LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@pytest.fixture
def no_outbound(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Refuse every connection that is not to this machine, and record it."""
    attempted: list[str] = []
    real_create_connection = socket.create_connection

    def guarded(address: tuple[str, int], *args: object, **kwargs: object) -> socket.socket:
        host = address[0]
        attempted.append(host)
        if host not in LOCAL_HOSTS:
            message = f"the test suite tried to connect to {host}"
            raise AssertionError(message)
        return real_create_connection(address, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket, "create_connection", guarded)
    yield attempted


def test_a_crawl_over_the_local_site_contacts_nobody_else(no_outbound: list[str]) -> None:
    """The realistic shape: a page whose links point off this machine."""
    from datetime import UTC, datetime

    from maxicrawler.web.session import CrawlOptions, CrawlSession

    site = Site()
    site.add_html("/", '<a href="https://example.org/somewhere">out</a><a href="/a">a</a>')
    site.add_html("/a", "<p>x</p>")
    service = WebDiscoveryService(
        DiscoveryPipeline(EventBus()),
        fetcher=UrllibPageFetcher(user_agent="MaxiCrawler/test", timeout=5.0),
    )

    with serve(site) as base:
        session = CrawlSession(
            session_id="guard",
            seed_url=f"{base}/",
            started_at=datetime.now(UTC),
            options=CrawlOptions(max_depth=1, same_domain=True),
        )
        report = CrawlEngine(service).run(session)

    assert report.statistics.pages_visited == 2
    assert set(no_outbound) <= LOCAL_HOSTS


def test_the_guard_itself_refuses_a_remote_address(no_outbound: list[str]) -> None:
    """Without this, a green suite would prove nothing about the one above."""
    with pytest.raises(AssertionError, match="tried to connect to"):
        socket.create_connection(("example.org", 80), timeout=1)
