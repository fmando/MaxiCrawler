"""Tests for the layout, the navigation and the dashboard."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from web_server import Site, serve

from maxicrawler import __version__
from maxicrawler.api import create_app
from maxicrawler.api.jobs import CrawlJobs
from maxicrawler.api.routes import SECTIONS, STATIC_DIRECTORY, TEMPLATES
from maxicrawler.app import CrawlService
from maxicrawler.config import Settings
from maxicrawler.database import SQLiteCrawlRepository, SQLiteDatabase
from maxicrawler.web.report import CrawlReport, CrawlStatistics
from maxicrawler.web.session import CrawlOptions, CrawlSession, CrawlState

STARTED = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


@contextmanager
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Yield a client over an application with a throwaway database."""
    service = CrawlService(
        Settings(user_agent="MaxiCrawler/test", database_path=tmp_path / "urls.db")
    )
    jobs = CrawlJobs(service, persist=False)
    with TestClient(create_app(service=service, jobs=jobs)) as test_client:
        yield test_client


def record_crawl(tmp_path: Path, *, seed: str, state: CrawlState, pages: int) -> None:
    """Write one finished crawl into the database the interface reads."""
    from maxicrawler.crawler import DiscoverySummary, PluginUsage
    from maxicrawler.domain import ScanSession, Statistics

    repository = SQLiteCrawlRepository(SQLiteDatabase(tmp_path / "urls.db"))
    repository.initialize()
    session = CrawlSession(
        session_id=f"crawl-{seed}",
        seed_url=seed,
        started_at=STARTED,
        options=CrawlOptions(max_depth=2, max_pages=50, same_domain=True),
    )
    repository.start_crawl(session)
    repository.finish_crawl(
        session,
        CrawlReport(
            session=session,
            state=state,
            statistics=CrawlStatistics(pages_visited=pages, elapsed_seconds=6.2),
            summary=DiscoverySummary(
                session=ScanSession(session.session_id, STARTED),
                statistics=Statistics(discovered_urls=412),
                plugin_usage=(PluginUsage("generic", 400), PluginUsage("mega", 12)),
            ),
            pages=(),
            finished_at=STARTED,
        ),
    )


# --- the layout --------------------------------------------------------------


def test_every_section_is_reachable_from_the_first_page(tmp_path: Path) -> None:
    """The navigation is whole from the start, so no later page rearranges it."""
    with client(tmp_path) as test_client:
        body = test_client.get("/").text

        for section in SECTIONS:
            assert f">{section.label}</a>" in body


@pytest.mark.parametrize("path", ["/", "/crawls", "/library", "/settings"])
def test_every_section_answers(tmp_path: Path, path: str) -> None:
    with client(tmp_path) as test_client:
        response = test_client.get(path)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.parametrize(
    ("path", "label"),
    [("/", "Dashboard"), ("/crawls", "Crawls"), ("/library", "Library"), ("/settings", "Settings")],
)
def test_the_page_you_are_on_is_marked(tmp_path: Path, path: str, label: str) -> None:
    with client(tmp_path) as test_client:
        body = test_client.get(path).text

    assert re.search(rf'class="active">{label}</a>', body)
    assert body.count('class="active"') == 1


def test_the_layout_carries_the_version(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        assert __version__ in test_client.get("/").text


def test_the_layout_says_who_is_responsible(tmp_path: Path) -> None:
    """robots.txt is not consulted, and the page must not stay quiet about it."""
    with client(tmp_path) as test_client:
        assert "robots.txt" in test_client.get("/").text


def test_the_stylesheet_is_served(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        response = test_client.get("/static/maxicrawler.css")

    assert response.status_code == 200
    assert "css" in response.headers["content-type"]
    assert "--accent" in response.text


def test_the_page_links_to_the_stylesheet(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        assert "/static/maxicrawler.css" in test_client.get("/").text


def test_nothing_is_loaded_from_another_host(tmp_path: Path) -> None:
    """A local interface must work with no outbound request at all."""
    with client(tmp_path) as test_client:
        body = test_client.get("/").text

    for attribute in ("src=", "href="):
        for match in re.findall(rf'{attribute}"([^"]+)"', body):
            assert not match.startswith(("http://", "https://", "//")), match


# --- the dashboard -----------------------------------------------------------


def test_an_installation_that_has_crawled_nothing_says_so(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        body = test_client.get("/").text

    assert "No crawls recorded yet" in body


def test_recorded_crawls_are_listed(tmp_path: Path) -> None:
    record_crawl(tmp_path, seed="https://example.test/", state=CrawlState.COMPLETED, pages=28)

    with client(tmp_path) as test_client:
        body = test_client.get("/").text

    assert "https://example.test/" in body
    assert ">28<" in body
    assert "completed" in body


def test_a_listed_crawl_shows_what_it_was_told_to_do(tmp_path: Path) -> None:
    record_crawl(tmp_path, seed="https://example.test/", state=CrawlState.COMPLETED, pages=3)

    with client(tmp_path) as test_client:
        body = test_client.get("/").text

    assert "depth 2" in body
    assert "same domain" in body


def test_a_crawl_that_hit_the_ceiling_is_badged_differently(tmp_path: Path) -> None:
    record_crawl(tmp_path, seed="https://example.test/", state=CrawlState.PAGE_LIMIT, pages=50)

    with client(tmp_path) as test_client:
        body = test_client.get("/").text

    assert "page limit" in body
    assert 'class="badge warn"' in body


# --- the placeholders --------------------------------------------------------


def test_the_library_says_plainly_that_it_holds_nothing_yet(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        body = test_client.get("/library").text

    assert "does not download anything yet" in body


def test_a_placeholder_still_carries_the_whole_layout(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        body = test_client.get("/library").text

    assert "MaxiCrawler" in body
    assert ">Dashboard</a>" in body


# --- packaging ---------------------------------------------------------------


def test_the_templates_sit_beside_the_code(tmp_path: Path) -> None:
    """A checkout that works and an installed wheel that does not is the trap."""
    directory = Path(TEMPLATES.env.loader.searchpath[0])  # type: ignore[union-attr]

    assert (directory / "base.html").is_file()
    assert directory.parent.name == "api"


def test_the_stylesheet_sits_beside_the_code() -> None:
    assert (STATIC_DIRECTORY / "maxicrawler.css").is_file()
    assert STATIC_DIRECTORY.parent.name == "api"


# --- starting a crawl --------------------------------------------------------


@contextmanager
def live_client(tmp_path: Path) -> Iterator[TestClient]:
    """Yield a client whose crawls really run, against a local site."""
    service = CrawlService(
        Settings(
            user_agent="MaxiCrawler/test",
            database_path=tmp_path / "urls.db",
            network_timeout=5.0,
        )
    )
    jobs = CrawlJobs(service, persist=False)
    try:
        with TestClient(create_app(service=service, jobs=jobs)) as test_client:
            yield test_client
    finally:
        jobs.shutdown()


def wait_until_finished(test_client: TestClient, job_id: str, *, timeout: float = 20.0) -> str:
    """Return the crawl page once the crawl behind it has finished."""
    from time import monotonic, sleep

    deadline = monotonic() + timeout
    while monotonic() < deadline:
        body = test_client.get(f"/crawls/{job_id}").text
        if "running" not in body and "queued" not in body:
            return body
        sleep(0.05)
    raise AssertionError("the crawl did not finish in time")


def test_the_form_offers_the_configured_defaults(tmp_path: Path) -> None:
    """The same defaults the CLI applies, through the same service."""
    with client(tmp_path) as test_client:
        body = test_client.get("/").text

    assert 'name="depth" value="0"' in body
    assert 'name="max_pages" value="50"' in body
    assert 'name="same_domain" value="1">' in body  # unchecked


def test_starting_a_crawl_redirects_to_it(tmp_path: Path) -> None:
    """A redirect, so reloading afterwards does not start a second crawl."""
    site = Site()
    site.add_html("/", '<a href="/a">a</a>')
    site.add_html("/a", "<p>x</p>")

    with live_client(tmp_path) as test_client, serve(site) as base:
        response = test_client.post(
            "/crawls",
            data={"url": f"{base}/", "depth": "1", "max_pages": "10", "same_domain": "1"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"].startswith("/crawls/")
        wait_until_finished(test_client, response.headers["location"].rsplit("/", 1)[1])


def test_a_started_crawl_can_be_watched(tmp_path: Path) -> None:
    site = Site()
    site.add_html("/", '<a href="/a">a</a>')
    site.add_html("/a", "<p>x</p>")

    with live_client(tmp_path) as test_client, serve(site) as base:
        response = test_client.post(
            "/crawls", data={"url": f"{base}/", "depth": "1", "same_domain": "1"}
        )
        job_id = str(response.url).rsplit("/", 1)[1]
        body = wait_until_finished(test_client, job_id)

    assert f"{base}/" in body
    assert "Pages read" in body
    assert "completed" in body


def test_the_crawl_page_is_rendered_by_the_server(tmp_path: Path) -> None:
    """A reload is a complete way to follow a crawl; a stream only adds ease."""
    site = Site()
    site.add_html("/", "<p>x</p>")

    with live_client(tmp_path) as test_client, serve(site) as base:
        response = test_client.post("/crawls", data={"url": f"{base}/", "same_domain": "1"})
        job_id = str(response.url).rsplit("/", 1)[1]
        body = wait_until_finished(test_client, job_id)

    assert "<script" not in body
    assert 'id="pages-visited"' in body


def test_an_unknown_crawl_is_not_found(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        assert test_client.get("/crawls/nope").status_code == 404


# --- a form that cannot be honoured ------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"url": "", "depth": "1"}, "unsupported URL scheme"),
        ({"url": "example.test", "depth": "1"}, "unsupported URL scheme"),
        ({"url": "file:///etc/passwd"}, "unsupported URL scheme"),
        ({"url": "https://example.test/", "depth": "-1"}, "max_depth must not be negative"),
        ({"url": "https://example.test/", "max_pages": "0"}, "max_pages must be at least 1"),
        ({"url": "https://example.test/", "depth": "two"}, "depth must be a whole number"),
        ({"url": "https://example.test/", "max_pages": "lots"}, "max pages must be a whole number"),
    ],
)
def test_a_form_that_cannot_be_honoured_says_why(
    tmp_path: Path, data: dict[str, str], expected: str
) -> None:
    with client(tmp_path) as test_client:
        response = test_client.post("/crawls", data=data)

    assert response.status_code == 400
    assert expected in response.text


def test_a_rejected_form_keeps_what_was_typed(tmp_path: Path) -> None:
    """Losing a pasted URL because the depth was wrong is a small rudeness."""
    with client(tmp_path) as test_client:
        response = test_client.post(
            "/crawls",
            data={"url": "https://example.test/deep/page", "depth": "-1", "same_domain": "1"},
        )

    assert 'value="https://example.test/deep/page"' in response.text
    assert 'name="same_domain" value="1" checked' in response.text


def test_a_rejected_form_starts_no_crawl(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        test_client.post("/crawls", data={"url": "not-a-url"})
        registry = test_client.app.state.jobs  # type: ignore[attr-defined]

    assert registry.recent() == ()


def test_an_empty_number_falls_back_to_the_configured_default(tmp_path: Path) -> None:
    site = Site()
    site.add_html("/", "<p>x</p>")

    with live_client(tmp_path) as test_client, serve(site) as base:
        response = test_client.post(
            "/crawls", data={"url": f"{base}/", "depth": "", "max_pages": ""}
        )
        job_id = str(response.url).rsplit("/", 1)[1]
        wait_until_finished(test_client, job_id)
        registry = test_client.app.state.jobs  # type: ignore[attr-defined]

    options = registry.get(job_id).session.options
    assert options.max_depth == 0
    assert options.max_pages == 50


def test_a_recorded_crawl_links_to_its_page(tmp_path: Path) -> None:
    record_crawl(tmp_path, seed="https://example.test/", state=CrawlState.COMPLETED, pages=3)

    with client(tmp_path) as test_client:
        body = test_client.get("/").text

    assert 'href="/crawls/crawl-https://example.test/"' in body


def test_a_body_that_is_not_a_form_is_refused_clearly(tmp_path: Path) -> None:
    """Quietly seeing no fields at all would look like an empty submission."""
    with client(tmp_path) as test_client:
        response = test_client.post("/crawls", json={"url": "https://example.test/"})

    assert response.status_code == 415


def test_a_repeated_field_takes_the_last_value(tmp_path: Path) -> None:
    """What a browser means when a form somehow submits a name twice."""
    with client(tmp_path) as test_client:
        response = test_client.post(
            "/crawls",
            content="url=https%3A%2F%2Fexample.test%2F&depth=1&depth=-1",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )

    assert response.status_code == 400
    assert "max_depth must not be negative" in response.text


def test_a_url_with_a_query_survives_the_form(tmp_path: Path) -> None:
    """Percent-encoding is exactly what the standard parser is for."""
    site = Site()
    site.add_html("/search", "<p>x</p>")

    with live_client(tmp_path) as test_client, serve(site) as base:
        response = test_client.post(
            "/crawls", data={"url": f"{base}/search?q=a%20b&n=1", "same_domain": "1"}
        )
        job_id = str(response.url).rsplit("/", 1)[1]
        wait_until_finished(test_client, job_id)
        registry = test_client.app.state.jobs  # type: ignore[attr-defined]

    assert registry.get(job_id).session.seed_url.endswith("?q=a%20b&n=1")
