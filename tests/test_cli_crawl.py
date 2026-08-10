"""Tests for the crawl command and its renderers."""

import ast
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner
from web_server import Site, serve

from maxicrawler.cli import app
from maxicrawler.cli.crawling import (
    EXIT_CRAWLED,
    EXIT_FETCH_FAILED,
    EXIT_INTERRUPTED,
    EXIT_NOT_A_PAGE,
    MAX_LISTED_PAGES,
    exit_code_for,
    render_crawl,
    render_crawl_json,
)
from maxicrawler.crawler import DiscoverySummary, PluginUsage
from maxicrawler.database import SQLiteCrawlRepository, SQLiteDatabase
from maxicrawler.domain import ScanSession, Statistics
from maxicrawler.web import LinkKind
from maxicrawler.web.report import CrawlReport, CrawlStatistics, PageOutcome, SkipReason
from maxicrawler.web.session import CrawlOptions, CrawlSession, CrawlState, RequestContext

runner = CliRunner()
MEGA_LINK = "https://mega.nz/file/AaBbCcDd#0123456789abcdefghijklmnopqrstuvwxyzABC"
STARTED = datetime(2026, 8, 7, tzinfo=UTC)

# The Mega link is here so a plugin has something to classify. Every test that
# crawls this tree recursively restricts the scope, so the link is discovered
# and counted but never fetched: the suite must not leave this machine, and
# with --any-domain as the default that is a fixture's responsibility.
TREE = {
    "/": f'<a href="/a">a</a><a href="/b">b</a><a href="{MEGA_LINK}">share</a>',
    "/a": '<a href="/a1">a1</a><a href="/">home</a>',
    "/b": '<a href="/a1">a1 too</a>',
    "/a1": "<p>leaf</p>",
}


# Every invocation below passes --allow-private, because the site under test is
# on 127.0.0.1 and the shipped default refuses to crawl this machine. The flag
# is the escape an operator crawling their own network would use, so the suite
# uses it rather than turning the guard off.


def make_site(pages: dict[str, str] | None = None) -> Site:
    """Return a local site serving *pages*."""
    site = Site()
    for path, markup in (pages if pages is not None else TREE).items():
        site.add_html(path, markup)
    return site


# --- the renderers, without a network ----------------------------------------


def make_report(
    *,
    state: CrawlState = CrawlState.COMPLETED,
    options: CrawlOptions | None = None,
    pages: tuple[PageOutcome, ...] = (),
    statistics: CrawlStatistics | None = None,
    context: RequestContext | None = None,
) -> CrawlReport:
    """Return a crawl report without running a crawl."""
    session = CrawlSession(
        session_id="crawl-1",
        seed_url="https://example.test/",
        started_at=STARTED,
        options=options or CrawlOptions(max_depth=2, max_pages=50),
        context=context or RequestContext(user_agent="MaxiCrawler/test"),
    )
    summary = DiscoverySummary(
        session=ScanSession("crawl-1", STARTED),
        statistics=Statistics(documents_processed=3, discovered_urls=30, duplicate_urls=7),
        plugin_usage=(PluginUsage("generic", 28), PluginUsage("mega", 2)),
    )
    return CrawlReport(
        session=session,
        state=state,
        statistics=statistics or CrawlStatistics(pages_visited=3, elapsed_seconds=6.25),
        summary=summary,
        pages=pages,
        finished_at=STARTED,
    )


def page(url: str, depth: int = 0, **kwargs: object) -> PageOutcome:
    """Return a page outcome for *url*."""
    values: dict[str, object] = {"status": 200, "final_url": url}
    values.update(kwargs)
    return PageOutcome(url=url, depth=depth, **values)  # type: ignore[arg-type]


def test_the_report_heads_with_the_seed_and_what_was_asked_for() -> None:
    text = render_crawl(make_report())

    assert (
        "Crawl:     https://example.test/  "
        "(depth 2, any domain, max 50 pages, robots.txt obeyed)" in text
    )
    assert "Finished:  completed in 6.2s" in text


def test_the_report_names_a_domain_restriction() -> None:
    text = render_crawl(make_report(options=CrawlOptions(max_depth=1, same_domain=True)))

    assert "same domain" in text


def test_the_report_names_included_subdomains() -> None:
    options = CrawlOptions(max_depth=1, same_domain=True, include_subdomains=True)

    assert "same domain and subdomains" in render_crawl(make_report(options=options))


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (CrawlState.COMPLETED, "completed"),
        (CrawlState.PAGE_LIMIT, "stopped at the page limit"),
        (CrawlState.INTERRUPTED, "interrupted"),
    ],
)
def test_every_ending_is_named_in_words(state: CrawlState, expected: str) -> None:
    assert expected in render_crawl(make_report(state=state))


def test_a_single_page_crawl_keeps_its_detail() -> None:
    text = render_crawl(
        make_report(
            options=CrawlOptions(max_depth=0),
            pages=(page("https://example.test/", title="Example Domain"),),
        )
    )

    assert "Fetched:   https://example.test/" in text
    assert "Status:    200" in text
    assert "Title:     Example Domain" in text


def test_a_single_page_crawl_names_a_redirect() -> None:
    text = render_crawl(
        make_report(pages=(page("https://example.test/old", final_url="https://example.test/new"),))
    )

    assert "Final URL: https://example.test/new" in text


def test_a_single_page_crawl_reports_a_canonical_claim() -> None:
    text = render_crawl(
        make_report(pages=(page("https://example.test/a", canonical_url="https://example.test/b"),))
    )

    assert "Canonical: https://example.test/b" in text


def test_a_multi_page_crawl_lists_one_line_each() -> None:
    pages = (
        page("https://example.test/", 0),
        page("https://example.test/a", 1),
        page("https://example.test/gone", 1, status=None, error="HTTP 404", final_url=None),
    )

    text = render_crawl(
        make_report(pages=pages, statistics=CrawlStatistics(pages_visited=2, pages_failed=1))
    )

    assert "Pages visited: 2" in text
    assert "200  d0  https://example.test/" in text
    assert "err  d1  https://example.test/gone  (failed)" in text
    assert "Pages failed: 1" in text


def test_a_long_page_list_is_summarized() -> None:
    pages = tuple(page(f"https://example.test/{index}", 1) for index in range(MAX_LISTED_PAGES + 5))

    text = render_crawl(make_report(pages=pages))

    assert "... and 5 more" in text


def test_the_report_names_why_urls_were_skipped() -> None:
    statistics = CrawlStatistics(
        pages_visited=3,
        pages_skipped=128,
        skips_by_reason=(
            (SkipReason.OUT_OF_SCOPE, 96),
            (SkipReason.ALREADY_SEEN, 30),
            (SkipReason.TOO_DEEP, 2),
        ),
    )
    pages = (page("https://example.test/", 0), page("https://example.test/a", 1))

    text = render_crawl(make_report(pages=pages, statistics=statistics))

    assert "Pages skipped: 128" in text
    assert "  out of scope: 96" in text
    assert "  already seen: 30" in text
    assert "  too deep: 2" in text


def test_the_report_groups_links_by_how_they_were_written() -> None:
    statistics = CrawlStatistics(
        pages_visited=3,
        links_by_kind=((LinkKind.ANCHOR, 30), (LinkKind.IMAGE, 6), (LinkKind.TEXT, 1)),
    )

    text = render_crawl(make_report(statistics=statistics))

    assert "Links found: 37" in text
    assert "  anchor: 30" in text
    assert "  image: 6" in text
    assert "  plain text: 1" in text


def test_the_report_ends_with_the_shared_discovery_summary() -> None:
    text = render_crawl(make_report())

    assert "Documents processed: 3" in text
    assert "Unique URLs: 30" in text
    assert "Duplicates removed: 7" in text
    assert "generic: 28" in text
    assert "mega: 2" in text


# --- exit codes --------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "code"),
    [
        (CrawlState.COMPLETED, EXIT_CRAWLED),
        (CrawlState.PAGE_LIMIT, EXIT_CRAWLED),
        (CrawlState.INTERRUPTED, EXIT_INTERRUPTED),
    ],
)
def test_a_limit_is_not_a_failure_but_an_interruption_is_reported(
    state: CrawlState, code: int
) -> None:
    assert exit_code_for(make_report(state=state)) == code


# --- the JSON document -------------------------------------------------------


def test_the_json_report_states_the_crawl_and_its_options() -> None:
    document = json.loads(render_crawl_json(make_report()))

    assert document["seed_url"] == "https://example.test/"
    assert document["state"] == "completed"
    assert document["options"] == {
        "max_depth": 2,
        "max_pages": 50,
        "same_domain": False,
        "include_subdomains": False,
    }


def test_the_json_report_lists_every_page_with_both_urls() -> None:
    pages = (page("https://example.test/old", 0, final_url="https://example.test/new"),)

    document = json.loads(render_crawl_json(make_report(pages=pages)))

    assert document["pages"][0]["url"] == "https://example.test/old"
    assert document["pages"][0]["final_url"] == "https://example.test/new"
    assert document["pages"][0]["depth"] == 0


def test_the_json_report_carries_the_counters() -> None:
    statistics = CrawlStatistics(
        pages_visited=3,
        pages_skipped=5,
        skips_by_reason=((SkipReason.TOO_DEEP, 5),),
        links_by_kind=((LinkKind.ANCHOR, 30),),
        max_depth_reached=2,
    )

    document = json.loads(render_crawl_json(make_report(statistics=statistics)))

    assert document["statistics"]["pages_visited"] == 3
    assert document["statistics"]["skips_by_reason"] == {"too deep": 5}
    assert document["statistics"]["links_by_kind"] == {"anchor": 30}
    assert document["discovery"]["unique_urls"] == 30


def test_the_json_report_never_carries_the_request_context() -> None:
    """The other place besides the database where a credential could escape."""
    context = RequestContext.of(
        user_agent="MaxiCrawler/test",
        headers={"Authorization": "Bearer SuperSecretValue"},
    )

    document = render_crawl_json(make_report(context=context))

    assert "SuperSecretValue" not in document
    assert "Authorization" not in document
    assert "user_agent" not in document


def test_the_renderer_reads_no_request_context() -> None:
    """Asserted from the syntax tree, so widening it has to be deliberate."""
    tree = ast.parse(Path("src/maxicrawler/cli/crawling.py").read_text(encoding="utf-8"))

    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

    assert "context" not in attributes
    assert "headers" not in attributes


# --- the command -------------------------------------------------------------


def test_one_page_is_crawled_by_default() -> None:
    site = make_site()

    with serve(site) as base:
        result = runner.invoke(app, ["crawl", "--allow-private", f"{base}/", "--no-persist"])

    assert result.exit_code == EXIT_CRAWLED
    assert "depth 0" in result.stdout
    assert "Fetched:   " in result.stdout
    assert "Documents processed: 1" in result.stdout


def test_a_depth_follows_links() -> None:
    site = make_site()

    with serve(site) as base:
        result = runner.invoke(
            app,
            [
                "crawl",
                "--allow-private",
                f"{base}/",
                "--same-domain",
                "--depth",
                "2",
                "--no-persist",
            ],
        )

    assert result.exit_code == EXIT_CRAWLED
    assert "Pages visited: 4" in result.stdout
    assert "mega: 1" in result.stdout


def test_the_short_depth_flag_works() -> None:
    site = make_site()

    with serve(site) as base:
        result = runner.invoke(
            app,
            ["crawl", "--allow-private", f"{base}/", "-d", "1", "--same-domain", "--no-persist"],
        )

    assert "Pages visited: 3" in result.stdout


def test_external_links_are_followed_unless_told_otherwise() -> None:
    """ "Elsewhere" is this same server under its other hostname.

    127.0.0.1 and localhost are one machine but two hosts, so the scope rule is
    exercised without the suite ever reaching the internet.
    """
    site = Site()

    with serve(site) as base:
        elsewhere = f"http://localhost:{base.rsplit(':', 1)[1]}"
        site.add_html("/", f'<a href="{elsewhere}/away">away</a>')
        site.add_html("/away", "<p>elsewhere</p>")
        result = runner.invoke(
            app, ["crawl", "--allow-private", f"{base}/", "--depth", "1", "--no-persist"]
        )

    assert "out of scope" not in result.stdout
    assert "Pages visited: 2" in result.stdout


def test_same_domain_keeps_the_crawl_at_home() -> None:
    site = make_site()

    with serve(site) as base:
        result = runner.invoke(
            app,
            [
                "crawl",
                "--allow-private",
                f"{base}/",
                "--depth",
                "2",
                "--same-domain",
                "--no-persist",
            ],
        )

    assert "same domain" in result.stdout
    assert "out of scope: 1" in result.stdout


def test_the_page_ceiling_is_honoured_and_named() -> None:
    site = make_site()

    with serve(site) as base:
        result = runner.invoke(
            app,
            [
                "crawl",
                "--allow-private",
                f"{base}/",
                "--same-domain",
                "--depth",
                "3",
                "--max-pages",
                "2",
                "--no-persist",
            ],
        )

    assert result.exit_code == EXIT_CRAWLED
    assert "stopped at the page limit" in result.stdout
    assert "Pages visited: 2" in result.stdout


def test_a_recursive_crawl_can_report_json() -> None:
    site = make_site()

    with serve(site) as base:
        result = runner.invoke(
            app,
            [
                "crawl",
                "--allow-private",
                f"{base}/",
                "--same-domain",
                "--depth",
                "1",
                "--json",
                "--no-persist",
            ],
        )
        document = json.loads(result.stdout)

        assert document["seed_url"] == f"{base}/"

    assert len(document["pages"]) == 3
    assert document["state"] == "completed"


def test_the_crawl_summary_is_persisted(tmp_path: Path) -> None:
    config = tmp_path / "maxicrawler.toml"
    database = tmp_path / "urls.db"
    config.write_text(f'[maxicrawler]\ndatabase_path = "{database.as_posix()}"\n', encoding="utf-8")
    site = make_site()

    with serve(site) as base:
        result = runner.invoke(
            app,
            [
                "crawl",
                "--allow-private",
                f"{base}/",
                "--same-domain",
                "--depth",
                "1",
                "--config",
                str(config),
            ],
        )

    assert result.exit_code == EXIT_CRAWLED
    stored = SQLiteCrawlRepository(SQLiteDatabase(database)).stored_crawls()
    assert len(stored) == 1
    assert stored[0].pages_visited == 3
    assert stored[0].state is CrawlState.COMPLETED


def test_the_defaults_are_configurable(tmp_path: Path) -> None:
    """The domain restriction stays opt-in, but an installation may flip it."""
    config = tmp_path / "maxicrawler.toml"
    config.write_text(
        "[maxicrawler]\ncrawl_depth = 2\ncrawl_max_pages = 2\ncrawl_same_domain = true\n",
        encoding="utf-8",
    )
    site = make_site()

    with serve(site) as base:
        result = runner.invoke(
            app, ["crawl", "--allow-private", f"{base}/", "--config", str(config), "--no-persist"]
        )

    assert "depth 2, same domain, max 2 pages" in result.stdout


def test_a_flag_overrides_the_configured_default(tmp_path: Path) -> None:
    config = tmp_path / "maxicrawler.toml"
    config.write_text("[maxicrawler]\ncrawl_same_domain = true\n", encoding="utf-8")
    site = make_site()

    with serve(site) as base:
        result = runner.invoke(
            app,
            [
                "crawl",
                "--allow-private",
                f"{base}/",
                "--any-domain",
                "--config",
                str(config),
                "--no-persist",
            ],
        )

    assert "any domain" in result.stdout


def test_one_broken_page_does_not_fail_the_command() -> None:
    site = make_site({"/": '<a href="/gone">gone</a><a href="/a">a</a>', "/a": "<p>x</p>"})

    with serve(site) as base:
        result = runner.invoke(
            app,
            [
                "crawl",
                "--allow-private",
                f"{base}/",
                "--same-domain",
                "--depth",
                "1",
                "--no-persist",
            ],
        )

    assert result.exit_code == EXIT_CRAWLED
    assert "Pages failed: 1" in result.stdout


def test_a_missing_seed_exits_with_the_fetch_code() -> None:
    site = make_site()

    with serve(site) as base:
        result = runner.invoke(app, ["crawl", "--allow-private", f"{base}/nope", "--no-persist"])

    assert result.exit_code == EXIT_FETCH_FAILED
    assert "HTTP 404" in result.stderr


def test_a_seed_that_is_not_a_page_has_its_own_exit_code() -> None:
    site = make_site()
    site.add("/data.json", body=b"{}", content_type="application/json")

    with serve(site) as base:
        result = runner.invoke(
            app, ["crawl", "--allow-private", f"{base}/data.json", "--no-persist"]
        )

    assert result.exit_code == EXIT_NOT_A_PAGE
    assert "not a page" in result.stderr


def test_a_non_http_url_is_rejected_as_a_bad_argument() -> None:
    result = runner.invoke(app, ["crawl", "--allow-private", "file:///etc/passwd", "--no-persist"])

    assert result.exit_code == 2
    assert "unsupported URL scheme" in result.stderr


def test_an_impossible_depth_is_refused() -> None:
    result = runner.invoke(
        app, ["crawl", "--allow-private", "https://example.test/", "--depth", "-1"]
    )

    assert result.exit_code != 0


def test_prose_urls_can_be_turned_off() -> None:
    site = make_site({"/": f"<p>{MEGA_LINK}</p>"})

    with serve(site) as base:
        with_prose = runner.invoke(app, ["crawl", "--allow-private", f"{base}/", "--no-persist"])
        without = runner.invoke(
            app, ["crawl", "--allow-private", f"{base}/", "--no-prose", "--no-persist"]
        )

    assert "Links found: 1" in with_prose.stdout
    assert "Links found: 0" in without.stdout


def test_the_configured_user_agent_is_sent() -> None:
    site = make_site()

    with serve(site) as base:
        runner.invoke(app, ["crawl", "--allow-private", f"{base}/", "--no-persist"])

    assert "MaxiCrawler" in site.requests[0].headers["User-Agent"]


def test_the_report_explains_a_ceiling_that_pages_do_not_add_up_to() -> None:
    statistics = CrawlStatistics(pages_visited=46, pages_failed=0, pages_attempted=50)
    pages = tuple(page(f"https://example.test/{index}", 1) for index in range(46))

    text = render_crawl(make_report(pages=pages, statistics=statistics))

    assert "Pages visited: 46" in text
    assert "Pages attempted: 50" in text


def test_the_report_stays_quiet_when_every_request_produced_a_page() -> None:
    statistics = CrawlStatistics(pages_visited=2, pages_failed=1, pages_attempted=3)
    pages = (page("https://example.test/", 0), page("https://example.test/a", 1))

    text = render_crawl(make_report(pages=pages, statistics=statistics))

    assert "Pages attempted" not in text


def test_the_json_report_carries_the_attempt_count() -> None:
    statistics = CrawlStatistics(pages_visited=46, pages_failed=0, pages_attempted=50)

    document = json.loads(render_crawl_json(make_report(statistics=statistics)))

    assert document["statistics"]["pages_attempted"] == 50


def test_a_file_link_is_reported_but_never_requested() -> None:
    """The behaviour a real crawl of a sheet-music site was losing 44% to."""
    site = make_site({"/": '<a href="/sheet.pdf">sheet</a><a href="/a">a</a>', "/a": "<p>x</p>"})
    site.add("/sheet.pdf", body=b"%PDF-1.7", content_type="application/pdf")

    with serve(site) as base:
        result = runner.invoke(
            app,
            [
                "crawl",
                "--allow-private",
                f"{base}/",
                "--depth",
                "1",
                "--same-domain",
                "--no-persist",
            ],
        )

    assert result.exit_code == EXIT_CRAWLED
    assert "Pages visited: 2" in result.stdout
    assert "Pages failed" not in result.stdout
    assert "not a page link: 1" in result.stdout
    assert "/sheet.pdf" not in {request.path for request in site.requests}


# --- responsible crawling from the command line ------------------------------


def test_a_forbidden_page_is_reported_as_skipped_rather_than_fetched() -> None:
    site = make_site()
    site.add("/robots.txt", body=b"User-agent: *\nDisallow: /a\n", content_type="text/plain")

    with serve(site) as base:
        result = runner.invoke(
            app,
            [
                "crawl",
                "--allow-private",
                f"{base}/",
                "--depth",
                "2",
                "--same-domain",
                "--no-persist",
            ],
        )

    assert result.exit_code == EXIT_CRAWLED
    assert "disallowed by robots.txt" in result.stdout


def test_robots_can_be_ignored_for_one_run() -> None:
    """The escape has to be one flag, or the safe default becomes a nuisance."""
    site = make_site()
    site.add("/robots.txt", body=b"User-agent: *\nDisallow: /\n", content_type="text/plain")

    with serve(site) as base:
        refused = runner.invoke(app, ["crawl", "--allow-private", f"{base}/", "--no-persist"])
        allowed = runner.invoke(
            app, ["crawl", "--allow-private", f"{base}/", "--ignore-robots", "--no-persist"]
        )

    assert refused.exit_code == EXIT_FETCH_FAILED
    assert "robots.txt" in refused.stderr
    assert allowed.exit_code == EXIT_CRAWLED
    assert "Documents processed: 1" in allowed.stdout
    # And the report says which of the two it was, so a scrollback read later
    # is not a guess about what the configuration held at the time.
    assert "robots.txt ignored" in allowed.stdout


def test_this_machine_is_refused_without_the_flag() -> None:
    """The shipped default, from the command line."""
    with serve(make_site()) as base:
        result = runner.invoke(app, ["crawl", f"{base}/", "--no-persist"])

    assert result.exit_code == EXIT_FETCH_FAILED
    assert "private network" in result.stderr
