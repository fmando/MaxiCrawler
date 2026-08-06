"""Tests for the crawl command and its renderers."""

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner
from web_server import Site, serve

from maxicrawler.cli import app
from maxicrawler.cli.crawling import (
    EXIT_CRAWLED,
    EXIT_FETCH_FAILED,
    EXIT_NOT_A_PAGE,
    render_crawl,
    render_crawl_json,
)
from maxicrawler.crawler import DiscoverySummary, PluginUsage
from maxicrawler.domain import ScanSession, Statistics
from maxicrawler.web import CrawlResult, HtmlDocument, LinkKind, PageInfo, PageLink

runner = CliRunner()
MEGA_LINK = "https://mega.nz/file/AaBbCcDd#0123456789abcdefghijklmnopqrstuvwxyzABC"

PAGE = f"""
<html>
  <head><title>Example Domain</title></head>
  <body>
    <a href="/one">one</a>
    <a href="/two">two</a>
    <a href="/one">one again</a>
    <a href="{MEGA_LINK}">share</a>
    <img src="/pic.png">
    <a href="mailto:someone@example.test">mail</a>
  </body>
</html>
"""


# --- the renderers, without a network ----------------------------------------


def make_result(
    *,
    requested_url: str = "https://example.test/",
    final_url: str = "https://example.test/",
    redirects: tuple[str, ...] = (),
    base_url: str | None = None,
    title: str | None = "Example Domain",
    canonical_url: str | None = None,
    links: tuple[PageLink, ...] = (),
    skipped_links: int = 0,
    truncated: bool = False,
    content_encoding: str | None = None,
) -> CrawlResult:
    """Return a crawl result without fetching anything."""
    page = PageInfo(
        requested_url=requested_url,
        final_url=final_url,
        status=200,
        size=1256,
        encoding="utf-8",
        content_type="text/html",
        content_encoding=content_encoding,
        redirects=redirects,
    )
    document = HtmlDocument(
        url=final_url,
        base_url=base_url if base_url is not None else final_url,
        encoding="utf-8",
        title=title,
        canonical_url=canonical_url,
        links=links,
        skipped_links=skipped_links,
        truncated=truncated,
    )
    summary = DiscoverySummary(
        session=ScanSession("s1", datetime(2026, 8, 6, tzinfo=UTC)),
        statistics=Statistics(documents_processed=1, discovered_urls=30, duplicate_urls=7),
        plugin_usage=(PluginUsage("generic", 28), PluginUsage("mega", 2)),
    )
    return CrawlResult(page=page, document=document, summary=summary)


def make_link(url: str, kind: LinkKind = LinkKind.ANCHOR) -> PageLink:
    """Return a resolved link of *kind*."""
    return PageLink(raw_url=url, resolved_url=url, kind=kind, tag="a", attribute="href")


def test_the_report_names_the_requested_url_and_what_came_back() -> None:
    report = render_crawl(make_result())

    assert "Fetched:   https://example.test/" in report
    assert "Status:    200 text/html (utf-8, 1256 bytes)" in report


def test_the_report_names_a_redirect_target() -> None:
    report = render_crawl(
        make_result(
            requested_url="http://example.test/old",
            final_url="https://www.example.test/new",
            redirects=("https://www.example.test/new",),
        )
    )

    assert "Fetched:   http://example.test/old" in report
    assert "Redirects: 1 -> https://www.example.test/new" in report


def test_the_report_stays_quiet_when_nothing_redirected() -> None:
    assert "Redirects:" not in render_crawl(make_result())


def test_the_report_names_a_declared_base_url() -> None:
    report = render_crawl(make_result(base_url="https://cdn.test/a/"))

    assert "Base URL:  https://cdn.test/a/" in report


def test_the_report_stays_quiet_about_an_undeclared_base_url() -> None:
    assert "Base URL:" not in render_crawl(make_result())


def test_the_report_counts_links_by_kind() -> None:
    report = render_crawl(
        make_result(
            links=(
                make_link("https://example.test/a"),
                make_link("https://example.test/b"),
                make_link("https://example.test/c.png", LinkKind.IMAGE),
            ),
            skipped_links=5,
        )
    )

    assert "Links found: 3" in report
    assert "  anchor: 2" in report
    assert "  image: 1" in report
    assert "Skipped (not HTTP(S)): 5" in report


def test_the_report_omits_a_zero_skip_count() -> None:
    assert "Skipped" not in render_crawl(make_result())


def test_the_report_warns_when_the_link_limit_was_reached() -> None:
    assert "more links than the configured limit" in render_crawl(make_result(truncated=True))


def test_the_report_ends_with_the_shared_discovery_summary() -> None:
    report = render_crawl(make_result())

    assert "Documents processed: 1" in report
    assert "URLs discovered: 37" in report
    assert "Unique URLs: 30" in report
    assert "Duplicates removed: 7" in report
    assert "Plugin usage:" in report
    assert "generic: 28" in report
    assert "mega: 2" in report


def test_the_report_names_a_content_encoding_when_one_was_used() -> None:
    report = render_crawl(make_result(content_encoding="gzip"))

    assert "(utf-8, gzip, 1256 bytes)" in report


def test_the_json_report_states_both_urls() -> None:
    document = json.loads(
        render_crawl_json(
            make_result(
                requested_url="http://example.test/old",
                final_url="https://www.example.test/new",
                redirects=("https://www.example.test/new",),
            )
        )
    )

    assert document["requested_url"] == "http://example.test/old"
    assert document["final_url"] == "https://www.example.test/new"
    assert document["redirects"] == ["https://www.example.test/new"]


def test_the_json_report_lists_every_link_with_its_kind() -> None:
    result = make_result(links=(make_link("https://example.test/a.png", LinkKind.IMAGE),))

    document = json.loads(render_crawl_json(result))

    assert document["links"] == [
        {
            "raw_url": "https://example.test/a.png",
            "url": "https://example.test/a.png",
            "kind": "image",
            "tag": "a",
            "attribute": "href",
        }
    ]


def test_the_json_report_carries_the_discovery_counters() -> None:
    document = json.loads(render_crawl_json(make_result()))

    assert document["discovery"]["unique_urls"] == 30
    assert document["discovery"]["plugin_usage"][0] == {"name": "generic", "count": 28}


# --- the command -------------------------------------------------------------


def test_the_command_reports_a_crawled_page(tmp_path: Path) -> None:
    site = Site()
    site.add_html("/", PAGE)

    with serve(site) as base:
        result = runner.invoke(app, ["crawl", f"{base}/", "--no-persist"])

    assert result.exit_code == EXIT_CRAWLED
    assert "Title:     Example Domain" in result.stdout
    assert "Links found: 5" in result.stdout
    assert "Documents processed: 1" in result.stdout
    assert "mega: 1" in result.stdout


def test_the_command_counts_duplicates_through_the_shared_pipeline() -> None:
    site = Site()
    site.add_html("/", PAGE)

    with serve(site) as base:
        result = runner.invoke(app, ["crawl", f"{base}/", "--no-persist"])

    assert "Unique URLs: 4" in result.stdout
    assert "Duplicates removed: 1" in result.stdout


def test_the_command_can_report_json() -> None:
    site = Site()
    site.add_html("/", PAGE)

    with serve(site) as base:
        result = runner.invoke(app, ["crawl", f"{base}/", "--no-persist", "--json"])
        document = json.loads(result.stdout)

        assert document["requested_url"] == f"{base}/"
        assert document["final_url"] == f"{base}/"

    assert document["title"] == "Example Domain"


def test_the_command_can_skip_prose_urls() -> None:
    site = Site()
    site.add_html("/", f"<p>{MEGA_LINK}</p>")

    with serve(site) as base:
        with_prose = runner.invoke(app, ["crawl", f"{base}/", "--no-persist"])
        without = runner.invoke(app, ["crawl", f"{base}/", "--no-persist", "--no-prose"])

    assert "Links found: 1" in with_prose.stdout
    assert "Links found: 0" in without.stdout


def test_the_command_persists_into_the_database(tmp_path: Path) -> None:
    config = tmp_path / "maxicrawler.toml"
    config.write_text(
        f'[maxicrawler]\ndatabase_path = "{(tmp_path / "urls.db").as_posix()}"\n',
        encoding="utf-8",
    )
    site = Site()
    site.add_html("/", PAGE)

    with serve(site) as base:
        result = runner.invoke(app, ["crawl", f"{base}/", "--config", str(config)])

    assert result.exit_code == EXIT_CRAWLED
    assert (tmp_path / "urls.db").exists()


def test_a_missing_page_exits_with_the_fetch_code() -> None:
    site = Site()

    with serve(site) as base:
        result = runner.invoke(app, ["crawl", f"{base}/nope", "--no-persist"])

    assert result.exit_code == EXIT_FETCH_FAILED
    assert "HTTP 404" in result.stderr


def test_an_unreachable_host_exits_with_the_fetch_code() -> None:
    result = runner.invoke(app, ["crawl", "http://127.0.0.1:1/", "--no-persist"])

    assert result.exit_code == EXIT_FETCH_FAILED


def test_a_response_that_is_not_a_page_has_its_own_exit_code() -> None:
    site = Site()
    site.add("/data.json", body=b"{}", content_type="application/json")

    with serve(site) as base:
        result = runner.invoke(app, ["crawl", f"{base}/data.json", "--no-persist"])

    assert result.exit_code == EXIT_NOT_A_PAGE
    assert "not a page" in result.stderr


def test_a_non_http_url_is_rejected_as_a_bad_argument() -> None:
    result = runner.invoke(app, ["crawl", "file:///etc/passwd", "--no-persist"])

    assert result.exit_code == 2
    assert "unsupported URL scheme" in result.stderr


def test_a_url_without_a_scheme_is_rejected_as_a_bad_argument() -> None:
    result = runner.invoke(app, ["crawl", "example.test", "--no-persist"])

    assert result.exit_code == 2


def test_the_configured_user_agent_is_sent() -> None:
    site = Site()
    site.add_html("/", PAGE)

    with serve(site) as base:
        runner.invoke(app, ["crawl", f"{base}/", "--no-persist"])

    assert "MaxiCrawler" in site.requests[0].headers["User-Agent"]


def test_the_configured_link_limit_is_honoured(tmp_path: Path) -> None:
    config = tmp_path / "maxicrawler.toml"
    config.write_text("[maxicrawler]\nmax_links = 2\n", encoding="utf-8")
    site = Site()
    site.add_html("/", PAGE)

    with serve(site) as base:
        result = runner.invoke(app, ["crawl", f"{base}/", "--config", str(config), "--no-persist"])

    assert "Links found: 2" in result.stdout
    assert "more links than the configured limit" in result.stdout


def test_the_configured_page_limit_is_honoured(tmp_path: Path) -> None:
    config = tmp_path / "maxicrawler.toml"
    config.write_text("[maxicrawler]\nmax_page_bytes = 32\n", encoding="utf-8")
    site = Site()
    site.add_html("/", PAGE)

    with serve(site) as base:
        result = runner.invoke(app, ["crawl", f"{base}/", "--config", str(config), "--no-persist"])

    assert result.exit_code == EXIT_FETCH_FAILED
