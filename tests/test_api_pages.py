"""Tests for the layout, the navigation and the dashboard."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

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
