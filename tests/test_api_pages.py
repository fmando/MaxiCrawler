"""Tests for the layout, the navigation and the dashboard."""

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import pytest
from doubles import StubProvider
from starlette.testclient import TestClient
from web_server import Site, serve

from maxicrawler import __version__
from maxicrawler.api import create_app
from maxicrawler.api.downloads import TransferQueue
from maxicrawler.api.jobs import CrawlJobs
from maxicrawler.api.routes import SECTIONS, STATIC_DIRECTORY, TEMPLATES
from maxicrawler.app import CrawlService, DownloadService, LibraryService, crawl_document
from maxicrawler.config import Settings
from maxicrawler.database import SQLiteCrawlRepository, SQLiteDatabase
from maxicrawler.domain import ContentDescriptor, ProviderCapability, ResourceRef
from maxicrawler.library import Library
from maxicrawler.providers import DownloadSink, ProviderRegistry
from maxicrawler.web.report import CrawlReport, CrawlStatistics
from maxicrawler.web.session import CrawlOptions, CrawlSession, CrawlState

STARTED = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
MEGA_KEY = "0123456789abcdefghijkl"
MEGA_URL = f"https://mega.nz/file/AaBbCcDd#{MEGA_KEY}"


@contextmanager
def client(
    tmp_path: Path, *, provider: StubProvider | None = None, max_view_bytes: int | None = None
) -> Iterator[TestClient]:
    """Yield a client over an application with a throwaway database and library.

    Both storage locations are below *tmp_path*, so no test can read or write
    what the machine running it happens to have in the working directory.
    """
    settings = Settings(
        user_agent="MaxiCrawler/test",
        database_path=tmp_path / "urls.db",
        library_path=tmp_path / "library",
        # The site these crawls reach is on loopback, which the shipped default
        # refuses; see tests/test_api_pages.py::test_a_private_address_is_refused
        # for the default's own behaviour.
        allow_private_networks=True,
        **({} if max_view_bytes is None else {"max_view_bytes": max_view_bytes}),
    )
    service = CrawlService(settings)
    jobs = CrawlJobs(service, persist=False)
    downloads = TransferQueue(
        DownloadService(
            settings,
            providers=None if provider is None else ProviderRegistry([provider]),
            library=Library(settings.library_path),
        )
    )
    application = create_app(
        service=service,
        jobs=jobs,
        downloads=downloads,
        library=LibraryService(settings, library=Library(settings.library_path)),
    )
    try:
        with TestClient(application) as test_client:
            yield test_client
    finally:
        downloads.shutdown()


def finished_download(test_client: TestClient, url: str = MEGA_URL) -> str:
    """Start a download and return its page once it has stopped moving.

    A transfer runs on a worker thread, so the redirect can arrive before the
    first byte does. The live block is rendered only while a download is
    unfinished, which makes its absence the page's own statement that there is
    nothing left to wait for.
    """
    response = test_client.post("/downloads", data={"url": url}, follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    deadline = monotonic() + 10
    while monotonic() < deadline:
        body = test_client.get(location).text
        if "download-live" not in body:
            return body
        sleep(0.01)
    raise AssertionError("the download did not finish within 10s")


class BlockingProvider(StubProvider):
    """A stub whose transfer waits until a test lets it finish."""

    def __init__(self) -> None:
        super().__init__(
            "mega",
            url_prefix="https://mega.nz/",
            capabilities=frozenset({ProviderCapability.INSPECT, ProviderCapability.DOWNLOAD}),
            payload=b"stub payload",
        )
        self.transferring = Event()
        self.release = Event()

    def download(self, ref: ResourceRef, sink: DownloadSink) -> ContentDescriptor:
        descriptor = ContentDescriptor(name="stub.bin", size=12)
        sink.begin(descriptor)
        sink.write(b"stub ")
        self.transferring.set()
        self.release.wait(timeout=10)
        sink.write(b"payload")
        return descriptor


def make_provider() -> StubProvider:
    """Return a stub provider that answers for Mega links and can transfer."""
    return StubProvider(
        "mega",
        url_prefix="https://mega.nz/",
        capabilities=frozenset({ProviderCapability.INSPECT, ProviderCapability.DOWNLOAD}),
        payload=b"stub payload",
    )


def record_crawl(
    tmp_path: Path, *, seed: str, state: CrawlState, pages: int, session_id: str | None = None
) -> None:
    """Write one finished crawl into the database the interface reads."""
    from maxicrawler.crawler import DiscoverySummary, PluginUsage
    from maxicrawler.domain import ScanSession, Statistics

    repository = SQLiteCrawlRepository(SQLiteDatabase(tmp_path / "urls.db"))
    repository.initialize()
    session = CrawlSession(
        session_id=session_id or f"crawl-{seed}",
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
    """And says it accurately.

    The footer claimed robots.txt was not consulted for two sprints after it
    was. Asserting the current sentence rather than the word alone is what
    makes the next such drift a failing test instead of a reading.
    """
    body = client_text(tmp_path, "/")

    assert "robots.txt is obeyed unless a crawl was told otherwise" in body
    assert "not consulted" not in body


def client_text(tmp_path: Path, path: str) -> str:
    """Return one page of a throwaway application."""
    with client(tmp_path) as test_client:
        return test_client.get(path).text


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


def test_an_empty_library_says_so_and_names_where_files_would_go(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        body = test_client.get("/library").text

    assert "Nothing has been downloaded yet" in body
    assert (tmp_path / "library").as_posix() in body


def test_the_library_page_carries_the_whole_layout(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        body = test_client.get("/library").text

    assert "MaxiCrawler" in body
    assert ">Dashboard</a>" in body


def test_the_library_can_be_searched(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        finished_download(test_client)

        found = test_client.get("/library?q=stub").text
        missed = test_client.get("/library?q=nowhere").text

    assert "stub.bin" in found
    assert "stub.bin" not in missed
    assert "Nothing matches that" in missed


def test_the_library_can_be_filtered_by_provider_and_status(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        finished_download(test_client)

        kept = test_client.get("/library?provider=mega&status=completed").text
        dropped = test_client.get("/library?provider=nobody").text

    assert "stub.bin" in kept
    assert "stub.bin" not in dropped


def test_the_library_can_be_sorted_by_a_link(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        finished_download(test_client)
        body = test_client.get("/library").text

        assert 'href="/library?sort=name&amp;dir=asc"' in body
        assert test_client.get("/library?sort=name&dir=asc").status_code == 200


def test_a_nonsense_query_string_still_answers(tmp_path: Path) -> None:
    """A stale bookmark is ordinary; a refusal would not be."""
    with client(tmp_path, provider=make_provider()) as test_client:
        finished_download(test_client)

        response = test_client.get("/library?sort=colour&dir=sideways&page=-4&status=maybe")

    assert response.status_code == 200
    assert "stub.bin" in response.text


def test_the_library_pages_and_says_where_it_is(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        finished_download(test_client)
        body = test_client.get("/library?per_page=1").text

    assert "page 1 of 1" in body


def test_a_row_links_to_the_file_it_describes(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        finished_download(test_client)
        body = test_client.get("/library").text

    assert re.search(r'href="/library/mega/[a-z0-9-]+"', body)


def test_the_library_lists_what_was_downloaded(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        finished_download(test_client)
        body = test_client.get("/library").text

    assert "stub.bin" in body
    assert ">mega</td>" in body
    assert "12 B" in body
    assert "https://mega.nz/file/AaBbCcDd" in body


def test_the_library_never_shows_a_decryption_key(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        finished_download(test_client)
        body = test_client.get("/library").text

    assert MEGA_KEY not in body


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
            allow_private_networks=True,
        )
    )
    jobs = CrawlJobs(service, persist=False)
    try:
        with TestClient(create_app(service=service, jobs=jobs)) as test_client:
            yield test_client
    finally:
        jobs.shutdown()


def wait_until_finished(test_client: TestClient, job_id: str, *, timeout: float = 20.0) -> str:
    """Return the crawl page once the crawl behind it has finished.

    Asked of the JSON endpoint rather than by reading words out of the page,
    which is both the exact condition the page changes on and impossible to
    fool: a report saying "7 still queued" used to read as a running crawl.

    A crawl that failed never produces a report, so the refusal is asked too.
    """
    from time import monotonic, sleep

    deadline = monotonic() + timeout
    while monotonic() < deadline:
        response = test_client.get(f"/crawls/{job_id}.json")
        if response.status_code == 200 or response.json()["finished"]:
            return test_client.get(f"/crawls/{job_id}").text
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
    assert "Pages read" in body
    assert "Discovered links" in body


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


# --- following a crawl live --------------------------------------------------


def slow_site(*, pages: int = 8, delay: float = 0.15) -> Site:
    """Return a site slow enough that "still running" is a fact, not a race."""
    site = Site()
    site.add_html("/", "".join(f'<a href="/p{index}">p{index}</a>' for index in range(pages)))
    for index in range(pages):
        site.add_html(f"/p{index}", "<p>x</p>", delay=delay)
    return site


def start(test_client: TestClient, base: str, **fields: str) -> str:
    """Start a crawl over *base* and return its identifier."""
    data = {"url": f"{base}/", "depth": "1", "max_pages": "50", "same_domain": "1"}
    data.update(fields)
    return str(test_client.post("/crawls", data=data).url).rsplit("/", 1)[1]


def test_the_client_script_is_served(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        response = test_client.get("/static/crawl.js")

    assert response.status_code == 200
    assert "EventSource" in response.text


def test_a_running_crawl_offers_the_stream_and_a_stop_button(tmp_path: Path) -> None:
    with live_client(tmp_path) as test_client, serve(slow_site()) as base:
        job_id = start(test_client, base)
        body = test_client.get(f"/crawls/{job_id}").text
        test_client.post(f"/crawls/{job_id}/stop")
        wait_until_finished(test_client, job_id)

    assert f'data-stream="/crawls/{job_id}/events"' in body
    assert "/static/crawl.js" in body
    assert "Stop</button>" in body


def test_a_running_page_carries_its_numbers_without_any_script(tmp_path: Path) -> None:
    """The script is an enhancement; the page has to be complete without it."""
    with live_client(tmp_path) as test_client, serve(slow_site()) as base:
        job_id = start(test_client, base)
        body = test_client.get(f"/crawls/{job_id}").text
        test_client.post(f"/crawls/{job_id}/stop")
        wait_until_finished(test_client, job_id)

    assert 'id="pages-visited"' in body
    assert "Pages read" in body
    assert "Links found" in body
    assert "of 50 pages" in body


def test_a_finished_crawl_offers_neither_stream_nor_stop(tmp_path: Path) -> None:
    site = Site()
    site.add_html("/", "<p>x</p>")

    with live_client(tmp_path) as test_client, serve(site) as base:
        job_id = start(test_client, base, depth="0")
        body = wait_until_finished(test_client, job_id)

    assert "crawl-live" not in body
    assert "<script" not in body
    assert "Stop</button>" not in body


def test_the_stream_reports_progress_and_ends(tmp_path: Path) -> None:
    with live_client(tmp_path) as test_client, serve(slow_site(pages=4)) as base:
        job_id = start(test_client, base)
        frames = []
        with test_client.stream("GET", f"/crawls/{job_id}/events") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-cache"
            for line in response.iter_lines():
                frames.append(line)
                if line == "event: finished":
                    break

    assert frames[0] == "event: progress"
    assert frames[-1] == "event: finished"


def test_the_stream_carries_what_the_page_shows(tmp_path: Path) -> None:
    """One rendering, two channels: no second formatter in JavaScript."""
    import json

    with live_client(tmp_path) as test_client, serve(slow_site(pages=3)) as base:
        job_id = start(test_client, base)
        payload = None
        with test_client.stream("GET", f"/crawls/{job_id}/events") as response:
            for line in response.iter_lines():
                if line.startswith("data: "):
                    payload = json.loads(line[6:])
                    break
        wait_until_finished(test_client, job_id)

    assert payload is not None
    assert "elapsed" in payload
    assert "progress_percent" in payload
    assert "state_label" in payload
    assert payload["elapsed"].endswith("s")


def test_the_stream_of_an_unknown_crawl_is_not_found(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        assert test_client.get("/crawls/nope/events").status_code == 404


# --- stopping ----------------------------------------------------------------


def test_stopping_redirects_back_to_the_crawl(tmp_path: Path) -> None:
    with live_client(tmp_path) as test_client, serve(slow_site()) as base:
        job_id = start(test_client, base)

        response = test_client.post(f"/crawls/{job_id}/stop", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == f"/crawls/{job_id}"
        wait_until_finished(test_client, job_id)


def test_stopping_ends_the_crawl(tmp_path: Path) -> None:
    with live_client(tmp_path) as test_client, serve(slow_site(pages=20)) as base:
        job_id = start(test_client, base)
        test_client.post(f"/crawls/{job_id}/stop")
        body = wait_until_finished(test_client, job_id)
        registry = test_client.app.state.jobs  # type: ignore[attr-defined]

    assert registry.get(job_id).snapshot().state is CrawlState.INTERRUPTED
    assert "stopped" in body


def test_stopping_a_crawl_that_does_not_exist_is_not_found(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        assert test_client.post("/crawls/nope/stop").status_code == 404


def test_the_server_answers_while_a_crawl_is_running(tmp_path: Path) -> None:
    """The property the whole worker-thread design exists to preserve."""
    with live_client(tmp_path) as test_client, serve(slow_site(pages=20)) as base:
        job_id = start(test_client, base)

        assert test_client.get("/health").json() == {"status": "ok"}
        assert test_client.get("/").status_code == 200

        test_client.post(f"/crawls/{job_id}/stop")
        wait_until_finished(test_client, job_id)


# --- what a finished crawl shows ---------------------------------------------

MEGA_LINK = "https://mega.nz/file/AaBbCcDd#0123456789abcdefghijklmnopqrstuvwxyzABC"


@contextmanager
def recording_client(tmp_path: Path, **overrides: object) -> Iterator[TestClient]:
    """Yield a client whose crawls write their URLs down, as a server's would.

    *overrides* reach :class:`Settings`. What they exist for is
    ``direct_downloads=False``: with the shipped default every HTTP link can be
    fetched, so "a link nothing can download" is a state only an
    inspection-only installation still has — and the interface has to keep
    behaving for that one.
    """
    service = CrawlService(
        Settings(
            user_agent="MaxiCrawler/test",
            database_path=tmp_path / "urls.db",
            library_path=tmp_path / "library",
            network_timeout=5.0,
            allow_private_networks=True,
            **overrides,  # type: ignore[arg-type]
        )
    )
    jobs = CrawlJobs(service, persist=True)
    try:
        with TestClient(create_app(service=service, jobs=jobs)) as test_client:
            yield test_client
    finally:
        jobs.shutdown()


def findable_site() -> Site:
    """Return a small site with something worth finding on it."""
    site = Site()
    site.add_html("/", f'<a href="/a">a</a><a href="{MEGA_LINK}">share</a><img src="/i.png">')
    site.add_html("/a", "<title>Second</title><p>x</p>")
    return site


def test_a_finished_crawl_shows_what_it_found(tmp_path: Path) -> None:
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = wait_until_finished(test_client, start(test_client, base))

    assert "Pages read" in body
    assert "Which plugin claimed each URL" in body
    assert "How links were written" in body
    assert "<th>Title</th>" in body  # the page table
    assert "Discovered links" in body


def test_a_finished_crawl_lists_the_pages_it_reached(tmp_path: Path) -> None:
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = wait_until_finished(test_client, start(test_client, base))

    assert f"{base}/a" in body
    assert "Second" in body


def test_a_finished_crawl_lists_what_it_discovered(tmp_path: Path) -> None:
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = wait_until_finished(test_client, start(test_client, base))

    assert MEGA_LINK.split("#")[0] in body
    assert ">mega</td>" in body


def test_a_mega_link_in_the_report_offers_a_download(tmp_path: Path) -> None:
    """The whole point of the report: from a found link to the file in one click."""
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = wait_until_finished(test_client, start(test_client, base))

    links = body.split("Discovered links", 1)[1]
    assert '<form class="row-action" method="post" action="/downloads">' in links
    assert f'name="url" value="{MEGA_LINK}"' in links


def test_an_ordinary_link_offers_a_download_too(tmp_path: Path) -> None:
    """What the direct provider is for. Before it, this row had no button.

    Every discovered link gets one now, which is the honest consequence of
    something claiming ordinary URLs -- and why the *type* filter, not this
    button, is what tells an image from a page.
    """
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = wait_until_finished(test_client, start(test_client, base))

    links = body.split("Discovered links", 1)[1]
    assert links.count("Download</button>") == 3
    assert 'name="url" value="http://127.0.0.1' in links


def test_the_download_button_carries_the_key_in_a_field_not_a_link(tmp_path: Path) -> None:
    """A fragment is the one part of a URL a browser never sends. A field is.

    What must not appear is a *link* that starts a download, since the key would
    be gone by the time the server saw it. The bare link to the queue page in the
    navigation is not one of those, so it is named here rather than swept up.
    """
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = wait_until_finished(test_client, start(test_client, base))

    links = re.findall(r'href="(/downloads[^"]*)"', body)
    assert links == ["/downloads"]  # the navigation, and nothing that carries a URL
    assert MEGA_LINK.split("#")[1] in body  # in the hidden field, which is sent


def test_a_report_of_ordinary_links_offers_no_download_where_nothing_may(
    tmp_path: Path,
) -> None:
    """An inspection-only installation, which `direct_downloads = false` makes.

    The column and the button disappear together rather than leaving a row of
    controls that would refuse. Worth keeping a test on: it is now the *only*
    way a report has nothing to offer, and it would otherwise rot unexercised.
    """
    site = Site()
    site.add_html("/", '<a href="/a">a</a>')
    site.add_html("/a", "<p>x</p>")

    with (
        recording_client(tmp_path, direct_downloads=False) as test_client,
        serve(site) as base,
    ):
        body = wait_until_finished(test_client, start(test_client, base))

    assert "Download</button>" not in body
    assert "<th>Action</th>" not in body


def test_the_link_table_puts_mega_above_the_generic_links(tmp_path: Path) -> None:
    """The one line this project exists to produce must not be below the fold."""
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = wait_until_finished(test_client, start(test_client, base))

    links = body.split("Discovered links", 1)[1]

    assert links.index(">mega</td>") < links.index(">generic</td>")


# --- navigating the report ---------------------------------------------------


def finished_report(test_client: TestClient, base: str, **params: str) -> str:
    """Return one finished crawl's report, asked with *params*."""
    job_id = start(test_client, base)
    wait_until_finished(test_client, job_id)
    return test_client.get(f"/crawls/{job_id}", params=params).text


def test_a_report_says_where_its_parts_are(tmp_path: Path) -> None:
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = wait_until_finished(test_client, start(test_client, base))

    assert 'class="jumps"' in body
    for anchor in ('id="summary"', 'id="pages"', 'id="links"'):
        assert anchor in body
    for jump in ('href="#summary"', 'href="#pages"', 'href="#links"'):
        assert jump in body


def test_the_breakdowns_fold_away(tmp_path: Path) -> None:
    """So the link table is on the first screen rather than the third."""
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = wait_until_finished(test_client, start(test_client, base))

    assert "<summary>How links were written</summary>" in body
    assert "<script" not in body


def test_a_report_can_be_filtered_by_plugin(tmp_path: Path) -> None:
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = finished_report(test_client, base, plugin="mega")

    links = body.split("Discovered links", 1)[1]

    assert MEGA_LINK.split("#")[0] in links
    assert ">generic</td>" not in links


def test_a_report_can_be_filtered_by_what_a_url_points_at(tmp_path: Path) -> None:
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = finished_report(test_client, base, target="image")

    links = body.split("Discovered links", 1)[1]

    assert "/i.png" in links
    assert MEGA_LINK.split("#")[0] not in links


def test_a_report_can_be_filtered_down_to_what_can_be_fetched(tmp_path: Path) -> None:
    """Still a filter, and on an inspection-only installation still a narrowing.

    With the direct provider on it matches everything, which is not a bug and
    not worth asserting -- what is worth asserting is that the filter still
    separates the two groups wherever there are two.
    """
    with (
        recording_client(tmp_path, direct_downloads=False) as test_client,
        serve(findable_site()) as base,
    ):
        body = finished_report(test_client, base, dl="yes")

    links = body.split("Discovered links", 1)[1]

    assert MEGA_LINK.split("#")[0] in links
    assert "/i.png" not in links


def test_the_fetchable_filter_matches_everything_once_ordinary_urls_count(
    tmp_path: Path,
) -> None:
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = finished_report(test_client, base, dl="yes")

    links = body.split("Discovered links", 1)[1]

    assert MEGA_LINK.split("#")[0] in links
    assert "/i.png" in links


def test_a_report_can_be_searched(tmp_path: Path) -> None:
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = finished_report(test_client, base, q="i.png")

    links = body.split("Discovered links", 1)[1]

    assert "/i.png" in links
    assert MEGA_LINK.split("#")[0] not in links


def test_a_filter_that_matches_nothing_says_so_and_offers_a_way_back(tmp_path: Path) -> None:
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = finished_report(test_client, base, q="nothing-is-called-this")

    assert "Nothing matches that" in body
    assert "Show everything" in body


def test_a_value_nobody_recognises_filters_nothing_rather_than_refusing(tmp_path: Path) -> None:
    """A report arrives from a bookmark; the default listing beats an error page."""
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        job_id = start(test_client, base)
        wait_until_finished(test_client, job_id)
        response = test_client.get(
            f"/crawls/{job_id}",
            params={"target": "wibble", "dl": "perhaps", "sort": "sideways", "page": "-3"},
        )

    assert response.status_code == 200
    assert MEGA_LINK.split("#")[0] in response.text


def test_a_page_past_the_end_answers_with_the_last_one(tmp_path: Path) -> None:
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        job_id = start(test_client, base)
        wait_until_finished(test_client, job_id)
        response = test_client.get(f"/crawls/{job_id}", params={"page": "99"})

    assert response.status_code == 200
    assert "Discovered links" in response.text


def test_a_column_can_be_turned_off(tmp_path: Path) -> None:
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = finished_report(test_client, base, hide="plugin,category")

    links = body.split("Discovered links", 1)[1]

    assert ">mega</td>" not in links
    assert MEGA_LINK.split("#")[0] in links  # the URL column stays


def test_a_column_name_nobody_recognises_is_ignored(tmp_path: Path) -> None:
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = finished_report(test_client, base, hide="wibble")

    assert ">mega</td>" in body.split("Discovered links", 1)[1]


def test_the_url_column_survives_being_asked_to_go(tmp_path: Path) -> None:
    """A table of discovered URLs without the URLs is not a narrower view."""
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = finished_report(test_client, base, hide="url")

    assert MEGA_LINK.split("#")[0] in body.split("Discovered links", 1)[1]


def test_the_page_table_can_be_narrowed_to_the_failures(tmp_path: Path) -> None:
    site = Site()
    site.add_html("/", '<a href="/a">a</a><a href="/gone">gone</a>')
    site.add_html("/a", "<title>Second</title><p>x</p>")

    with recording_client(tmp_path) as test_client, serve(site) as base:
        body = finished_report(test_client, base, pstate="failed")

    pages = body.split("Discovered links", 1)[0]

    assert "/gone" in pages
    assert f"{base}/a" not in pages


def test_the_page_table_can_be_searched(tmp_path: Path) -> None:
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = finished_report(test_client, base, pq="Second")

    pages = body.split("Discovered links", 1)[0]

    assert f"{base}/a" in pages
    assert ">1–1 of 1<" in pages.replace("\n", "").replace("  ", "")


def test_filtering_the_pages_leaves_the_link_filter_alone(tmp_path: Path) -> None:
    """Two tables on one URL, and neither may throw the other's question away."""
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = finished_report(test_client, base, plugin="mega", pstate="succeeded")

    links = body.split("Discovered links", 1)[1]
    pages = body.split("Discovered links", 1)[0]

    assert ">generic</td>" not in links  # the link filter still applies
    assert "200" in pages  # and the page filter applies too
    assert 'name="plugin" value="mega"' in links


def test_the_page_filter_carries_the_link_filter_in_its_own_links(tmp_path: Path) -> None:
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = finished_report(test_client, base, plugin="mega")

    pages = body.split("Discovered links", 1)[0]

    assert "plugin=mega" in pages


def test_an_unrecognised_page_filter_shows_everything(tmp_path: Path) -> None:
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        job_id = start(test_client, base)
        wait_until_finished(test_client, job_id)
        response = test_client.get(f"/crawls/{job_id}", params={"pstate": "sideways"})

    assert response.status_code == 200
    assert f"{base}/a" in response.text


def test_a_finished_crawl_offers_no_stop_button(tmp_path: Path) -> None:
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = wait_until_finished(test_client, start(test_client, base))

    assert "Stop</button>" not in body
    assert "<script" not in body


def test_a_crawl_that_recorded_nothing_says_so_rather_than_showing_nothing(
    tmp_path: Path,
) -> None:
    """An empty table would claim it found none, which is a different thing."""
    with live_client(tmp_path) as test_client, serve(findable_site()) as base:
        body = wait_until_finished(test_client, start(test_client, base))

    assert "recorded none of them" in body
    assert "without persistence" in body


# --- the same report as a document -------------------------------------------


def test_the_json_endpoint_answers_with_the_shared_document(tmp_path: Path) -> None:
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        job_id = start(test_client, base)
        wait_until_finished(test_client, job_id)
        document = test_client.get(f"/crawls/{job_id}.json").json()
        registry = test_client.app.state.jobs  # type: ignore[attr-defined]
        expected = crawl_document(registry.get(job_id).report)

    assert document == expected
    assert document["session_id"] == job_id


def test_the_json_endpoint_does_not_shadow_the_page(tmp_path: Path) -> None:
    """`{job_id}` would happily swallow the suffix if the order were wrong."""
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        job_id = start(test_client, base)
        wait_until_finished(test_client, job_id)

        assert "text/html" in test_client.get(f"/crawls/{job_id}").headers["content-type"]
        assert "json" in test_client.get(f"/crawls/{job_id}.json").headers["content-type"]


def test_a_running_crawl_has_no_document_yet(tmp_path: Path) -> None:
    with live_client(tmp_path) as test_client, serve(slow_site(pages=20)) as base:
        job_id = start(test_client, base)
        response = test_client.get(f"/crawls/{job_id}.json")
        test_client.post(f"/crawls/{job_id}/stop")
        wait_until_finished(test_client, job_id)

    assert response.status_code == 409
    assert response.json()["finished"] is False


def test_a_crawl_that_never_ran_says_it_will_never_have_one(tmp_path: Path) -> None:
    """A client must be able to tell "not yet" from "not ever"."""
    site = Site()
    site.default.status = 500

    with live_client(tmp_path) as test_client, serve(site) as base:
        job_id = start(test_client, base)
        wait_until_finished(test_client, job_id)
        payload = test_client.get(f"/crawls/{job_id}.json").json()

    assert payload["finished"] is True
    assert payload["error"]


def test_a_document_for_an_unknown_crawl_is_not_found(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        assert test_client.get("/crawls/nope.json").status_code == 404


# --- the crawls page ---------------------------------------------------------


def test_the_crawls_page_lists_the_whole_history(tmp_path: Path) -> None:
    """The dashboard shows the recent ones; this shows all of them."""
    for index in range(25):
        record_crawl(
            tmp_path, seed=f"https://example.test/{index}", state=CrawlState.COMPLETED, pages=3
        )

    with client(tmp_path) as test_client:
        body = test_client.get("/crawls").text

    assert "https://example.test/24" in body
    assert "https://example.test/0" in body


def test_the_crawls_page_says_when_there_is_nothing(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        body = test_client.get("/crawls").text

    assert "Nothing has been crawled yet" in body


def test_a_crawl_left_unfinished_by_a_restart_is_not_called_running(tmp_path: Path) -> None:
    """Nobody is behind it, so a page saying "running" would wait forever."""
    repository = SQLiteCrawlRepository(SQLiteDatabase(tmp_path / "urls.db"))
    repository.initialize()
    repository.start_crawl(
        CrawlSession(
            session_id="crawl-abandoned",
            seed_url="https://example.test/gone",
            started_at=STARTED,
            options=CrawlOptions(max_depth=1, max_pages=10),
        )
    )

    with client(tmp_path) as test_client:
        body = test_client.get("/crawls").text

    assert "abandoned" in body
    assert 'class="badge bad"' in body


def test_a_crawl_this_process_runs_is_called_running(tmp_path: Path) -> None:
    with recording_client(tmp_path) as test_client, serve(slow_site(pages=20)) as base:
        job_id = start(test_client, base)
        body = wait_for_the_row(test_client, job_id)
        test_client.post(f"/crawls/{job_id}/stop")
        wait_until_finished(test_client, job_id)

    assert "abandoned" not in body
    assert "running" in body


def wait_for_the_row(test_client: TestClient, job_id: str, *, timeout: float = 10.0) -> str:
    """Return the crawls page once *job_id* has reached it.

    The row is written by the worker thread as the crawl starts, so asking the
    instant after submitting is a race the request can lose. Waiting for the
    row is not waiting for the crawl: it has twenty slow pages ahead of it.
    """
    from time import monotonic, sleep

    deadline = monotonic() + timeout
    while monotonic() < deadline:
        body = test_client.get("/crawls").text
        if job_id in body:
            return body
        sleep(0.05)
    raise AssertionError("the crawl never reached the list")


# --- a crawl only the database remembers -------------------------------------


def test_a_recorded_crawl_still_has_a_page(tmp_path: Path) -> None:
    """After a restart this is every crawl, so the list must not lead nowhere."""
    record_crawl(
        tmp_path,
        seed="https://example.test/",
        state=CrawlState.COMPLETED,
        pages=28,
        session_id="old-crawl",
    )

    with client(tmp_path) as test_client:
        response = test_client.get("/crawls/old-crawl")

    assert response.status_code == 200
    assert "https://example.test/" in response.text


def test_a_recorded_crawl_says_what_the_record_cannot_hold(tmp_path: Path) -> None:
    record_crawl(
        tmp_path,
        seed="https://example.test/",
        state=CrawlState.COMPLETED,
        pages=28,
        session_id="old-crawl",
    )

    with client(tmp_path) as test_client:
        body = test_client.get("/crawls/old-crawl").text

    assert "This server did not run this crawl" in body
    assert "Pages read" in body
    assert "<th>Title</th>" not in body  # no per-page record exists


def test_a_recorded_crawl_lists_the_urls_it_kept(tmp_path: Path) -> None:
    with recording_client(tmp_path) as test_client, serve(findable_site()) as base:
        job_id = start(test_client, base)
        wait_until_finished(test_client, job_id)

    # A second server over the same database knows nothing of that crawl.
    with client(tmp_path) as fresh:
        body = fresh.get(f"/crawls/{job_id}").text

    assert MEGA_LINK.split("#")[0] in body
    assert ">mega</td>" in body


def test_a_crawl_nothing_knows_is_still_not_found(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        assert test_client.get("/crawls/nope").status_code == 404


def test_a_recorded_crawl_refuses_a_document_rather_than_inventing_one(tmp_path: Path) -> None:
    record_crawl(
        tmp_path,
        seed="https://example.test/",
        state=CrawlState.COMPLETED,
        pages=28,
        session_id="old-crawl",
    )

    with client(tmp_path) as test_client:
        response = test_client.get("/crawls/old-crawl.json")

    assert response.status_code == 409
    assert response.json()["finished"] is True
    assert "did not run that crawl" in response.json()["detail"]


# --- the settings page -------------------------------------------------------


def test_the_settings_page_shows_the_effective_configuration(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        body = test_client.get("/settings").text

    assert "user_agent" in body
    assert "MaxiCrawler/test" in body
    assert "crawl_max_pages" in body


def test_the_settings_page_shows_the_file_form_of_the_same_values(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        body = test_client.get("/settings").text

    # Unescaped, because the page is HTML and the quotes arrive as entities.
    # What matters is that the document reads back the way the file is written.
    document = unescape(body)

    assert "[maxicrawler]" in document
    assert 'user_agent = "MaxiCrawler/test"' in document
    assert "crawl_same_domain = false" in document


def test_a_configured_path_is_allowed_to_wrap(tmp_path: Path) -> None:
    """An absolute path in a `nowrap` cell held the whole page open sideways.

    Measured in a browser rather than guessed: with the value in `num`, which
    is `nowrap` so a column of figures stays aligned, the body scrolled 79px
    wider than the viewport and every table on the page went with it.
    """
    with client(tmp_path) as test_client:
        body = test_client.get("/settings").text

    assert 'class="value"' in body
    assert f'class="num">{tmp_path.as_posix()}' not in body


def test_the_settings_page_offers_no_way_to_change_anything(tmp_path: Path) -> None:
    """Read-only, and it must not look otherwise."""
    with client(tmp_path) as test_client:
        body = test_client.get("/settings").text

    assert "<form" not in body
    assert "<input" not in body


def test_the_settings_page_does_not_name_a_file_it_did_not_read(tmp_path: Path) -> None:
    """These settings were handed in, so pointing at maxicrawler.toml would mislead."""
    with client(tmp_path) as test_client:
        body = test_client.get("/settings").text

    assert "Set by whatever started this server" in body


def test_an_application_left_to_itself_reads_the_configuration_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The docstring said so long before the code did."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "maxicrawler.toml").write_text(
        '[maxicrawler]\nuser_agent = "MaxiCrawler/from-the-file"\ncrawl_max_pages = 7\n',
        encoding="utf-8",
    )

    with TestClient(create_app()) as test_client:
        body = test_client.get("/settings").text

    assert "MaxiCrawler/from-the-file" in body
    assert "Read from" in body
    assert "maxicrawler.toml" in body


def test_a_missing_configuration_file_is_named_as_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    with TestClient(create_app()) as test_client:
        body = test_client.get("/settings").text

    assert "There is no" in body
    assert "built-in defaults" in body


# --- the library page --------------------------------------------------------


def test_the_library_names_where_downloads_go(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        body = test_client.get("/library").text

    assert (tmp_path / "library").as_posix() in body


# --- downloading one link ----------------------------------------------------


def test_a_download_redirects_to_its_own_page(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        response = test_client.post("/downloads", data={"url": MEGA_URL}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/downloads/")


def test_a_finished_download_shows_the_file_and_the_way_to_the_library(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        body = finished_download(test_client)

    assert "stub.bin" in body
    assert "completed" in body
    assert 'href="/library"' in body
    assert "Open Library" in body


def test_a_download_page_is_rendered_by_the_server(tmp_path: Path) -> None:
    """Everything a reader needs is in the HTML; the script only saves reloading."""
    with client(tmp_path, provider=make_provider()) as test_client:
        body = finished_download(test_client)

    assert "12 B" in body
    assert "Transferred" in body
    # A finished download has nothing left to stream.
    assert "download-live" not in body


def test_a_download_page_never_shows_the_key(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        body = finished_download(test_client)

    assert MEGA_KEY not in body
    assert "https://mega.nz/file/AaBbCcDd" in body


def test_a_dead_link_says_why_rather_than_failing_the_request(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        page_body = finished_download(test_client, "https://example.test/file.iso")

    assert "failed" in page_body
    assert "no provider can handle this link" in page_body


def test_a_source_that_is_not_a_url_is_refused(tmp_path: Path) -> None:
    """A path would make the server read its own disk on somebody else's click."""
    with client(tmp_path, provider=make_provider()) as test_client:
        response = test_client.post(
            "/downloads", data={"url": str(tmp_path)}, follow_redirects=False
        )

    assert response.status_code == 400
    assert "not an absolute HTTP(S) URL" in unescape(response.text)


def test_an_unknown_download_is_not_found(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        assert test_client.get("/downloads/nothing").status_code == 404
        assert test_client.get("/downloads/nothing/events").status_code == 404
        assert test_client.post("/downloads/nothing/stop").status_code == 404


def test_a_finished_download_offers_no_stop_button(tmp_path: Path) -> None:
    """A button that cannot do anything is a button that teaches distrust."""
    with client(tmp_path, provider=make_provider()) as test_client:
        body = finished_download(test_client)

    assert "/stop" not in body


def test_stopping_a_download_redirects_back_to_its_page(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        started = test_client.post("/downloads", data={"url": MEGA_URL}, follow_redirects=False)
        location = started.headers["location"]

        response = test_client.post(f"{location}/stop", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == location


def test_the_download_script_is_served(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        response = test_client.get("/static/download.js")

    assert response.status_code == 200
    assert "EventSource" in response.text


def test_the_download_stream_reports_progress_and_ends(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        response = test_client.post("/downloads", data={"url": MEGA_URL}, follow_redirects=False)
        download_id = response.headers["location"].rsplit("/", 1)[-1]

        with test_client.stream("GET", f"/downloads/{download_id}/events") as stream:
            assert stream.headers["content-type"].startswith("text/event-stream")
            frames = "".join(stream.iter_text())

    assert "event: progress" in frames
    assert "event: finished" in frames
    assert MEGA_KEY not in frames


@pytest.mark.parametrize("path", ["/", "/crawls", "/library", "/settings"])
def test_no_page_loads_anything_from_another_host(tmp_path: Path, path: str) -> None:
    """A local interface must work with no outbound request at all."""
    record_crawl(tmp_path, seed="https://example.test/", state=CrawlState.COMPLETED, pages=3)

    with client(tmp_path) as test_client:
        body = test_client.get(path).text

    for attribute in ("src=", "href="):
        for match in re.findall(rf'{attribute}"([^"]+)"', body):
            assert not match.startswith(("http://", "https://", "//")), match


# --- one stored file ---------------------------------------------------------


def stored_item(test_client: TestClient) -> str:
    """Download something and return the path of its library page."""
    body = finished_download(test_client)
    match = re.search(r'href="(/library/[a-z0-9-]+/[a-z0-9-]+)"', body)
    assert match, "a finished download should link to its own page"
    return match.group(1)


def test_a_finished_download_links_straight_to_its_file(tmp_path: Path) -> None:
    """Landing in a list to search through would be the lesser answer."""
    with client(tmp_path, provider=make_provider()) as test_client:
        body = finished_download(test_client)

    assert "Show the file" in body
    assert re.search(r'href="/library/mega/[a-z0-9-]+"', body)


def test_the_detail_page_states_what_is_known_about_the_file(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        body = test_client.get(stored_item(test_client)).text

    assert "stub.bin" in body
    assert ">mega</td>" in body
    assert "12 B" in body
    assert "https://mega.nz/file/AaBbCcDd" in body
    assert "SHA-256" in body
    assert "completed" in body


def test_the_detail_page_shows_the_path_in_a_field_it_can_be_copied_from(
    tmp_path: Path,
) -> None:
    """A `file://` link would be blocked; the server launching Explorer is worse."""
    with client(tmp_path, provider=make_provider()) as test_client:
        where = stored_item(test_client)
        body = unescape(test_client.get(where).text)
        listing = unescape(test_client.get("/library").text)

    assert 'class="path"' in body
    assert "readonly" in body
    assert 'data-copy=".path"' in body
    assert "file://" not in body
    # Native separators, and the same spelling in the table as in the field: a
    # path somebody pastes into a file manager has one right form per platform.
    stored = next((tmp_path / "library").rglob("stub.bin"))
    assert str(stored) in body
    assert str(stored) in listing


def test_the_detail_page_never_shows_a_key(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        body = test_client.get(stored_item(test_client)).text

    assert MEGA_KEY not in body


def test_the_detail_page_offers_the_bytes(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        where = stored_item(test_client)
        body = test_client.get(where).text

        assert f'href="{where}/file"' in body
        response = test_client.get(f"{where}/file")

    assert response.status_code == 200
    assert response.content == b"stub payload"
    assert response.headers["content-type"] == "application/octet-stream"
    assert "attachment" in response.headers["content-disposition"]
    assert "stub.bin" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_a_download_route_never_invites_a_browser_to_render(tmp_path: Path) -> None:
    """Whatever the file is, this route says nothing that could be rendered."""
    with client(tmp_path, provider=make_provider()) as test_client:
        response = test_client.get(f"{stored_item(test_client)}/file")

    assert "html" not in response.headers["content-type"]
    assert "inline" not in response.headers["content-disposition"]


def test_a_file_whose_payload_vanished_is_not_offered(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        where = stored_item(test_client)
        for payload in (tmp_path / "library").rglob("stub.bin"):
            payload.unlink()

        body = test_client.get(where).text
        response = test_client.get(f"{where}/file")

    assert response.status_code == 404
    assert "moved or" in body
    assert "Download</a>" not in body


@pytest.mark.parametrize(
    "where",
    [
        "/library/mega/nothing",
        "/library/nobody/aabbccdd-0000000000",
        "/library/../../secret",
        "/library/mega/..%2f..%2fsecret",
        "/library/MEGA/AABBCCDD",
    ],
)
def test_a_file_that_cannot_be_addressed_is_not_found(tmp_path: Path, where: str) -> None:
    """One answer for a malformed name and an absent one, deliberately."""
    with client(tmp_path) as test_client:
        assert test_client.get(where).status_code == 404
        assert test_client.get(f"{where}/file").status_code == 404


def test_the_detail_page_carries_a_way_back(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        body = test_client.get(stored_item(test_client)).text

    assert 'class="crumbs"' in body
    assert 'href="/library"' in body


def test_the_copy_script_is_served(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        response = test_client.get("/static/copy.js")

    assert response.status_code == 200
    assert "clipboard" in response.text


# --- showing a file in the browser -------------------------------------------


def store(tmp_path: Path, filename: str, payload: bytes = b"hello") -> str:
    """Write one finished library entry by hand and return its page path."""
    from maxicrawler.domain import DownloadStatus, ResourceKind, ResourceRef
    from maxicrawler.library import new_record

    library = Library(tmp_path / "library")
    library.initialize()
    ref = ResourceRef(
        provider="mega",
        resource_id="AaBbCcDd",
        kind=ResourceKind.FILE,
        url="https://mega.nz/file/AaBbCcDd",
    )
    entry = library.entry(ref)
    stored = entry.content_path(filename)
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(payload)
    document = new_record(
        ref, entry.key, status=DownloadStatus.COMPLETED, name=filename
    ).to_document()
    document["downloaded_at"] = STARTED.isoformat()
    document["content"] = {
        "filename": filename,
        "path": f"content/{filename}",
        "size": len(payload),
        "checksums": [{"algorithm": "sha256", "value": "abc"}],
    }
    entry.path.mkdir(parents=True, exist_ok=True)
    entry.metadata_path.write_text(json.dumps(document), encoding="utf-8")
    return f"/library/mega/{entry.key}"


@pytest.mark.parametrize(
    ("filename", "content_type", "element"),
    [
        ("notes.txt", "text/plain; charset=utf-8", "<iframe"),
        ("readme.md", "text/plain; charset=utf-8", "<iframe"),
        ("Jump.pdf", "application/pdf", "<iframe"),
        ("photo.png", "image/png", "<img"),
        ("drawing.svg", "image/svg+xml", "<img"),
        ("page.html", "text/html; charset=utf-8", "<iframe"),
    ],
)
def test_a_file_the_browser_can_show_is_shown(
    tmp_path: Path, filename: str, content_type: str, element: str
) -> None:
    where = store(tmp_path, filename)

    with client(tmp_path) as test_client:
        body = test_client.get(where).text
        response = test_client.get(f"{where}/view")

    assert f'{element} src="{where}/view"' in body
    assert response.status_code == 200
    assert response.headers["content-type"] == content_type
    assert "inline" in response.headers["content-disposition"]


@pytest.mark.parametrize("filename", ["page.html", "drawing.svg"])
def test_what_could_execute_script_is_sandboxed(tmp_path: Path, filename: str) -> None:
    """The one assertion that keeps a stored page out of our own origin."""
    where = store(tmp_path, filename, b"<script>fetch('/settings')</script>")

    with client(tmp_path) as test_client:
        response = test_client.get(f"{where}/view")

    policy = response.headers["content-security-policy"]
    assert "sandbox" in policy
    assert "default-src 'none'" in policy


@pytest.mark.parametrize("filename", ["notes.txt", "Jump.pdf", "photo.png", "page.html"])
def test_every_inline_answer_refuses_to_be_re_sniffed(tmp_path: Path, filename: str) -> None:
    where = store(tmp_path, filename)

    with client(tmp_path) as test_client:
        response = test_client.get(f"{where}/view")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("filename", ["Jump.pdf", "photo.png", "notes.txt"])
def test_what_cannot_execute_script_is_not_sandboxed(tmp_path: Path, filename: str) -> None:
    """Measured, not assumed: Chrome refuses to render a PDF under that policy.

    `ERR_BLOCKED_BY_CLIENT`, because the directive blocks the plugin its viewer
    is. These three cannot reach our origin anyway — a PDF's own script runs in
    the browser's viewer, not in the page that framed it — so the policy would
    have cost the whole feature and bought nothing.
    """
    where = store(tmp_path, filename)

    with client(tmp_path) as test_client:
        response = test_client.get(f"{where}/view")

    assert "content-security-policy" not in response.headers


def test_a_frame_holding_script_capable_content_is_sandboxed_as_well(tmp_path: Path) -> None:
    where = store(tmp_path, "page.html")

    with client(tmp_path) as test_client:
        body = test_client.get(where).text

    assert "<iframe" in body
    assert "sandbox" in body.split("<iframe", 1)[1].split(">", 1)[0]


def test_a_pdf_frame_carries_no_sandbox_attribute(tmp_path: Path) -> None:
    """The attribute blocks Chrome's PDF viewer exactly as the header does."""
    where = store(tmp_path, "Jump.pdf")

    with client(tmp_path) as test_client:
        body = test_client.get(where).text

    assert "<iframe" in body
    assert "sandbox" not in body.split("<iframe", 1)[1].split(">", 1)[0]


def test_an_svg_is_never_framed(tmp_path: Path) -> None:
    """An `<img>` runs no script even when the file behind it contains some."""
    where = store(tmp_path, "drawing.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>')

    with client(tmp_path) as test_client:
        body = test_client.get(where).text

    assert f'<img src="{where}/view"' in body
    assert "<iframe" not in body


def test_a_type_nothing_can_show_says_so_and_offers_the_download(tmp_path: Path) -> None:
    where = store(tmp_path, "ubuntu.iso")

    with client(tmp_path) as test_client:
        body = test_client.get(where).text
        response = test_client.get(f"{where}/view")

    assert "can show" in body
    assert f'href="{where}/file"' in body
    assert "<iframe" not in body
    assert response.status_code == 415


def test_a_file_above_the_limit_is_offered_rather_than_shown(tmp_path: Path) -> None:
    where = store(tmp_path, "Jump.pdf", b"x" * 200)

    with client(tmp_path, max_view_bytes=100) as test_client:
        body = unescape(test_client.get(where).text)
        response = test_client.get(f"{where}/view")

    assert "above the viewer's" in body
    assert f'href="{where}/file"' in body
    assert response.status_code == 415


def test_a_viewer_answer_supports_a_range_request(tmp_path: Path) -> None:
    """What a PDF viewer uses to seek instead of fetching the whole file."""
    where = store(tmp_path, "Jump.pdf", b"0123456789")

    with client(tmp_path) as test_client:
        response = test_client.get(f"{where}/view", headers={"Range": "bytes=2-5"})

    assert response.status_code == 206
    assert response.content == b"2345"


def test_the_viewer_refuses_what_it_cannot_address(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        assert test_client.get("/library/mega/nothing/view").status_code == 404
        assert test_client.get("/library/../../secret/view").status_code == 404


# --- comfort around the edges -------------------------------------------------


def test_a_running_download_is_visible_from_elsewhere(tmp_path: Path) -> None:
    """Navigating away from a transfer should not mean losing it."""
    provider = BlockingProvider()
    with client(tmp_path, provider=provider) as test_client:
        response = test_client.post("/downloads", data={"url": MEGA_URL}, follow_redirects=False)
        assert provider.transferring.wait(timeout=10)
        try:
            dashboard = test_client.get("/").text
            library = test_client.get("/library").text
        finally:
            provider.release.set()
        test_client.get(response.headers["location"])

    for body in (dashboard, library):
        assert 'class="running"' in body
        assert "Watch it" in body


def test_nothing_is_announced_when_nothing_is_running(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        assert 'class="running"' not in test_client.get("/").text
        assert 'class="running"' not in test_client.get("/library").text


def test_a_crawl_page_says_where_it_sits(tmp_path: Path) -> None:
    record_crawl(
        tmp_path,
        seed="https://example.test/",
        state=CrawlState.COMPLETED,
        pages=3,
        session_id="c1",
    )

    with client(tmp_path) as test_client:
        body = test_client.get("/crawls/c1").text

    assert 'class="crumbs"' in body
    assert 'href="/crawls"' in body


def test_a_running_download_page_shows_speed_and_what_is_left(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        body = finished_download(test_client)

    assert "Speed" in body
    # A finished transfer has nothing left to estimate, so that row is gone.
    assert "Remaining" not in body
