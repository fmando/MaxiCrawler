"""Tests for the crawl service every client shares."""

from pathlib import Path

import pytest
from typer.testing import CliRunner
from web_server import Site, serve

from maxicrawler.app import CrawlService
from maxicrawler.cli import app
from maxicrawler.config import Settings
from maxicrawler.events import CrawlFinished, EventBus, PageCrawled, UrlDiscovered
from maxicrawler.web import PolicyRefusedError, PolicyRule
from maxicrawler.web.engine import CrawlEngine
from maxicrawler.web.report import SkipReason
from maxicrawler.web.session import CrawlControl, CrawlState

runner = CliRunner()
MEGA_LINK = "https://mega.nz/file/AaBbCcDd#0123456789abcdefghijklmnopqrstuvwxyzABC"

TREE = {
    "/": f'<a href="/a">a</a><a href="/b">b</a><a href="{MEGA_LINK}">share</a>',
    "/a": '<a href="/a1">a1</a>',
    "/b": "<p>leaf</p>",
    "/a1": "<p>leaf</p>",
}


def make_site() -> Site:
    """Return the local site every test here crawls."""
    site = Site()
    for path, markup in TREE.items():
        site.add_html(path, markup)
    return site


def make_service(**settings: object) -> CrawlService:
    """Return a service over throwaway settings.

    Loopback is allowed, because the site every test here crawls is on
    127.0.0.1 and the shipped default refuses that. Stating it in one place
    rather than turning the guard off globally is the point: a test that wants
    the guard's own behaviour builds a service without this.
    """
    return CrawlService(make_settings(**settings))


def make_settings(**settings: object) -> Settings:
    """Return throwaway settings that may reach the local test server."""
    settings.setdefault("allow_private_networks", True)
    return Settings(user_agent="MaxiCrawler/test", **settings)  # type: ignore[arg-type]


# --- building a session ------------------------------------------------------


def test_a_session_takes_its_defaults_from_the_configuration() -> None:
    service = make_service(crawl_depth=3, crawl_max_pages=7, crawl_same_domain=True)

    session = service.build_session("https://example.test/")

    assert session.options.max_depth == 3
    assert session.options.max_pages == 7
    assert session.options.same_domain is True


def test_what_the_caller_states_overrides_the_configuration() -> None:
    service = make_service(crawl_depth=3, crawl_max_pages=7, crawl_same_domain=True)

    session = service.build_session(
        "https://example.test/", depth=1, max_pages=2, same_domain=False
    )

    assert session.options.max_depth == 1
    assert session.options.max_pages == 2
    assert session.options.same_domain is False


def test_a_session_carries_the_configured_user_agent() -> None:
    session = make_service().build_session("https://example.test/")

    assert session.context.user_agent == "MaxiCrawler/test"


def test_every_session_gets_its_own_identifier() -> None:
    service = make_service()

    first = service.build_session("https://example.test/")
    second = service.build_session("https://example.test/")

    assert first.session_id != second.session_id


def test_an_identifier_can_be_supplied() -> None:
    session = make_service().build_session("https://example.test/", session_id="given")

    assert session.session_id == "given"
    assert session.scan_session.session_id == "given"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "example.test", "", "mailto:a@b.test"])
def test_a_url_that_is_not_http_is_refused_as_a_value_error(url: str) -> None:
    """One rule, stated once. The CLI and the browser each present it their way."""
    with pytest.raises(ValueError):
        make_service().build_session(url)


def test_an_impossible_option_is_refused_as_a_value_error() -> None:
    with pytest.raises(ValueError, match="max_depth must not be negative"):
        make_service().build_session("https://example.test/", depth=-1)


# --- running -----------------------------------------------------------------


def test_a_crawl_returns_the_report_it_produced() -> None:
    service = make_service()

    with serve(make_site()) as base:
        report = service.run(
            service.build_session(f"{base}/", depth=1, same_domain=True), persist=False
        )

    assert report.state is CrawlState.COMPLETED
    assert report.pages_visited == 3


def test_every_run_builds_a_fresh_graph() -> None:
    """Two crawls must not share a DiscoveryPipeline, which is not thread-safe."""
    service = make_service()

    with serve(make_site()) as base:
        session = service.build_session(f"{base}/", depth=1, same_domain=True)
        first = service.build_engine(session, persist=False)
        second = service.build_engine(session, persist=False)

    assert first is not second
    assert first.frontier is not second.frontier
    assert first.visited is not second.visited


def test_one_bus_carries_both_the_pipeline_and_the_engine() -> None:
    """A watcher needs UrlDiscovered and PageCrawled on the same bus."""
    bus = EventBus()
    seen: list[str] = []
    bus.subscribe(UrlDiscovered, lambda event: seen.append("url"))
    bus.subscribe(PageCrawled, lambda event: seen.append("page"))
    bus.subscribe(CrawlFinished, lambda event: seen.append("done"))
    service = make_service()

    with serve(make_site()) as base:
        session = service.build_session(f"{base}/", depth=1, same_domain=True)
        service.run(session, persist=False, event_bus=bus)

    assert "url" in seen
    assert "page" in seen
    assert seen[-1] == "done"


def test_a_supplied_control_can_stop_a_crawl() -> None:
    """The same handle a Stop button will hold."""
    control = CrawlControl()
    control.request_stop()
    service = make_service()

    with serve(make_site()) as base:
        session = service.build_session(f"{base}/", depth=3, same_domain=True)
        report = service.run(session, persist=False, control=control)

    assert report.state is CrawlState.INTERRUPTED


def test_a_supplied_control_reports_the_terminal_state() -> None:
    control = CrawlControl()
    service = make_service()

    with serve(make_site()) as base:
        session = service.build_session(f"{base}/", depth=1, same_domain=True)
        service.run(session, persist=False, control=control)

    assert control.state is CrawlState.COMPLETED


def test_building_an_engine_returns_something_runnable() -> None:
    service = make_service()

    with serve(make_site()) as base:
        session = service.build_session(f"{base}/", same_domain=True)
        engine = service.build_engine(session, persist=False)

        assert isinstance(engine, CrawlEngine)
        assert engine.run(session).pages_visited == 1


def test_a_seed_that_cannot_be_read_raises() -> None:
    service = make_service()

    with serve(Site()) as base, pytest.raises(Exception, match="HTTP 404"):
        service.run(service.build_session(f"{base}/nope"), persist=False)


def test_the_scope_option_reaches_the_crawl() -> None:
    """The service states it on the session; the engine derives it from there.

    "Elsewhere" is this same server under its other hostname, so the rule is
    exercised without the suite leaving the machine.
    """
    service = make_service()
    site = make_site()

    with serve(site) as base:
        elsewhere = f"http://localhost:{base.rsplit(':', 1)[1]}"
        site.add_html("/", f'<a href="{elsewhere}/away">away</a><a href="/a">a</a>')
        site.add_html("/away", "<p>elsewhere</p>")

        confined = service.run(
            service.build_session(f"{base}/", depth=1, same_domain=True), persist=False
        )
        unrestricted = service.run(
            service.build_session(f"{base}/", depth=1, same_domain=False), persist=False
        )

    assert confined.pages_visited == 2
    assert unrestricted.pages_visited == 3


def test_persisting_writes_both_tables(tmp_path: Path) -> None:
    from maxicrawler.database import SQLiteCrawlRepository, SQLiteDatabase

    database = tmp_path / "urls.db"
    service = CrawlService(make_settings(database_path=database))

    with serve(make_site()) as base:
        session = service.build_session(f"{base}/", depth=1, same_domain=True)
        service.run(session, persist=True)

    stored = SQLiteCrawlRepository(SQLiteDatabase(database)).stored_crawl(session.session_id)
    assert stored is not None
    assert stored.pages_visited == 3


def test_not_persisting_writes_nothing(tmp_path: Path) -> None:
    database = tmp_path / "urls.db"
    service = CrawlService(make_settings(database_path=database))

    with serve(make_site()) as base:
        service.run(service.build_session(f"{base}/", same_domain=True), persist=False)

    assert not database.exists()


# --- reading back what a crawl recorded --------------------------------------


def test_a_recorded_crawl_can_be_asked_for_its_urls(tmp_path: Path) -> None:
    service = CrawlService(make_settings(database_path=tmp_path / "urls.db"))

    with serve(make_site()) as base:
        session = service.build_session(f"{base}/", depth=2, same_domain=True)
        report = service.run(session, persist=True)

    urls = service.discovered_urls(session.session_id)

    assert len(urls) == report.summary.unique_urls
    assert MEGA_LINK in {stored.record.raw_url for stored in urls}


def test_recorded_urls_name_the_plugin_that_claimed_them(tmp_path: Path) -> None:
    service = CrawlService(make_settings(database_path=tmp_path / "urls.db"))

    with serve(make_site()) as base:
        session = service.build_session(f"{base}/", depth=2, same_domain=True)
        service.run(session, persist=True)

    plugins = {stored.plugin_name for stored in service.discovered_urls(session.session_id)}

    assert "mega" in plugins


def test_a_crawl_that_did_not_persist_recorded_no_urls(tmp_path: Path) -> None:
    """Which the interface must not confuse with a crawl that found none."""
    service = CrawlService(make_settings(database_path=tmp_path / "urls.db"))

    with serve(make_site()) as base:
        session = service.build_session(f"{base}/", depth=2, same_domain=True)
        report = service.run(session, persist=False)

    assert service.discovered_urls(session.session_id) == ()
    assert report.summary.unique_urls > 0


def test_asking_for_an_unknown_crawl_is_not_an_error(tmp_path: Path) -> None:
    service = CrawlService(make_settings(database_path=tmp_path / "urls.db"))

    assert service.discovered_urls("no-such-crawl") == ()
    assert service.stored_crawl("no-such-crawl") is None


def test_a_crawl_outlives_the_process_that_ran_it(tmp_path: Path) -> None:
    """What lets a later server show a crawl it never started."""
    settings = make_settings(database_path=tmp_path / "urls.db")

    with serve(make_site()) as base:
        session = CrawlService(settings).build_session(f"{base}/", depth=1, same_domain=True)
        CrawlService(settings).run(session, persist=True)

    later = CrawlService(settings).stored_crawl(session.session_id)

    assert later is not None
    assert later.seed_url == f"{base}/"
    assert later.pages_visited == 3
    assert later.finished_at is not None


# --- responsible crawling, as the service assembles it ------------------------


def robots(document: str) -> dict[str, object]:
    """Return the route arguments serving *document* as a robots.txt."""
    return {"body": document.encode("utf-8"), "content_type": "text/plain"}


def test_a_crawl_obeys_the_robots_txt_of_the_host_it_visits() -> None:
    """The whole sprint, seen from outside: nothing here mentions robots.txt."""
    service = make_service()
    site = make_site()
    site.add("/robots.txt", **robots("User-agent: *\nDisallow: /a\n"))

    with serve(site) as base:
        report = service.run(
            service.build_session(f"{base}/", depth=2, same_domain=True), persist=False
        )

    assert "/a" not in [page.url.removeprefix(base) for page in report.pages]
    assert "/b" in [page.url.removeprefix(base) for page in report.pages]
    assert dict(report.statistics.skips_by_reason)[SkipReason.ROBOTS_TXT] >= 1


def test_a_forbidden_url_is_skipped_rather_than_reported_as_a_failure() -> None:
    service = make_service()
    site = make_site()
    site.add("/robots.txt", **robots("User-agent: *\nDisallow: /a\n"))

    with serve(site) as base:
        report = service.run(
            service.build_session(f"{base}/", depth=2, same_domain=True), persist=False
        )

    assert report.failures == ()


def test_the_rules_are_read_once_however_many_pages_are_crawled() -> None:
    service = make_service()
    site = make_site()
    site.add("/robots.txt", **robots("User-agent: *\n"))

    with serve(site) as base:
        service.run(service.build_session(f"{base}/", depth=2, same_domain=True), persist=False)

    assert [request.path for request in site.requests].count("/robots.txt") == 1


def test_a_host_with_no_robots_txt_is_crawled_as_before() -> None:
    """The common case: a 404 leaves a crawler free, and costs one request."""
    service = make_service()

    with serve(make_site()) as base:
        report = service.run(
            service.build_session(f"{base}/", depth=1, same_domain=True), persist=False
        )

    assert report.pages_visited == 3


def test_ignoring_robots_is_possible_and_recorded_on_the_session() -> None:
    service = make_service()
    site = make_site()
    site.add("/robots.txt", **robots("User-agent: *\nDisallow: /\n"))

    with serve(site) as base:
        session = service.build_session(f"{base}/", depth=1, same_domain=True, respect_robots=False)
        report = service.run(session, persist=False)

    assert session.options.respect_robots is False
    assert report.pages_visited == 3
    assert "/robots.txt" not in [request.path for request in site.requests]


def test_a_seed_its_own_site_forbids_ends_the_crawl_by_saying_so() -> None:
    service = make_service()
    site = make_site()
    site.add("/robots.txt", **robots("User-agent: *\nDisallow: /\n"))

    with serve(site) as base, pytest.raises(PolicyRefusedError) as refusal:
        service.run(service.build_session(f"{base}/"), persist=False)

    assert refusal.value.rule is PolicyRule.ROBOTS
    assert "robots.txt" in str(refusal.value)


def test_a_crawl_records_whether_it_obeyed_robots(tmp_path: Path) -> None:
    """A setting that has changed since cannot answer this; the row can."""
    from maxicrawler.database import SQLiteCrawlRepository, SQLiteDatabase

    database = tmp_path / "urls.db"
    service = CrawlService(make_settings(database_path=database))

    with serve(make_site()) as base:
        session = service.build_session(f"{base}/", respect_robots=False)
        service.run(session, persist=True)

    stored = SQLiteCrawlRepository(SQLiteDatabase(database)).stored_crawl(session.session_id)
    assert stored is not None
    assert stored.respect_robots is False


def test_this_machine_is_refused_by_default() -> None:
    """The shipped behaviour, asserted without the escape every other test uses.

    A URL now arrives from a browser, and a browser can be pointed at a form by
    any page it visits.
    """
    service = CrawlService(Settings(user_agent="MaxiCrawler/test"))

    with serve(make_site()) as base, pytest.raises(PolicyRefusedError) as refusal:
        service.run(service.build_session(f"{base}/"), persist=False)

    assert refusal.value.rule is PolicyRule.PRIVATE_NETWORK


def test_one_address_can_be_allowed_without_opening_the_rest() -> None:
    service = make_service(allow_private_networks=False, private_network_allowlist=("127.0.0.1",))

    with serve(make_site()) as base:
        report = service.run(service.build_session(f"{base}/"), persist=False)

    assert report.pages_visited == 1


# --- the CLI is a client of this service, not a second implementation --------


def test_the_cli_and_the_service_agree_on_the_same_crawl(tmp_path: Path) -> None:
    """The test this whole package exists for.

    If the command line ever grew a second way to wire a crawl, these two
    numbers would drift apart.
    """
    config = tmp_path / "maxicrawler.toml"
    config.write_text("[maxicrawler]\nallow_private_networks = true\n", encoding="utf-8")
    service = make_service()

    with serve(make_site()) as base:
        direct = service.run(
            service.build_session(f"{base}/", depth=2, same_domain=True), persist=False
        )
        result = runner.invoke(
            app,
            [
                "crawl",
                f"{base}/",
                "--depth",
                "2",
                "--same-domain",
                "--no-persist",
                "--config",
                str(config),
            ],
        )

    assert result.exit_code == 0
    assert f"Pages visited: {direct.pages_visited}" in result.stdout
    assert f"URLs discovered: {direct.links_discovered}" in result.stdout
    for usage in direct.summary.plugin_usage:
        assert f"{usage.name}: {usage.count}" in result.stdout
