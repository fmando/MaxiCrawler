"""Tests for the recursive crawl engine, against a local multi-page site."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from web_server import Site, serve

from maxicrawler.crawler import DiscoveryPipeline
from maxicrawler.events import EventBus
from maxicrawler.web import (
    ContentTypeError,
    LinkKind,
    PolicyRefusedError,
    UrllibPageFetcher,
    WebDiscoveryService,
)
from maxicrawler.web.engine import CrawlEngine
from maxicrawler.web.frontier import CrawlItem, FifoFrontier, visit_key
from maxicrawler.web.policy import PolicyDecision, PolicyRule, SameDomainPolicy
from maxicrawler.web.report import CrawlReport, SkipReason
from maxicrawler.web.session import CrawlControl, CrawlOptions, CrawlSession, CrawlState

MEGA_LINK = "https://mega.nz/file/AaBbCcDd#0123456789abcdefghijklmnopqrstuvwxyzABC"

TREE = {
    "/": '<a href="/a">a</a><a href="/b">b</a>',
    "/a": '<a href="/a1">a1</a><a href="/">home</a>',
    "/b": '<a href="/b1">b1</a>',
    "/a1": '<a href="/a2">a2</a>',
    "/b1": "<p>leaf</p>",
    "/a2": "<p>leaf</p>",
}


def make_site(pages: dict[str, str] | None = None) -> Site:
    """Return a local site serving *pages*, defaulting to the tree above."""
    site = Site()
    for path, markup in (pages if pages is not None else TREE).items():
        site.add_html(path, markup)
    return site


def make_engine(**kwargs: object) -> CrawlEngine:
    """Return an engine over a real fetcher with a short timeout."""
    service = WebDiscoveryService(
        DiscoveryPipeline(EventBus()),
        fetcher=UrllibPageFetcher(user_agent="MaxiCrawler/test", timeout=5.0),
    )
    return CrawlEngine(service, **kwargs)  # type: ignore[arg-type]


def make_session(seed: str, **options: object) -> CrawlSession:
    """Return a crawl session over *seed*."""
    return CrawlSession(
        session_id="crawl-1",
        seed_url=seed,
        started_at=datetime.now(UTC),
        options=CrawlOptions(**options),  # type: ignore[arg-type]
    )


@contextmanager
def crawl(
    site: Site, path: str = "/", *, engine: CrawlEngine | None = None, **options: object
) -> Iterator[tuple[CrawlReport, str]]:
    """Run a crawl over *site* and yield the report with the base URL."""
    with serve(site) as base:
        runner = engine if engine is not None else make_engine()
        yield runner.run(make_session(f"{base}{path}", **options)), base


def visited_paths(report: CrawlReport, base: str) -> list[str]:
    """Return the paths of every page the crawl fetched, in order."""
    return [page.url.removeprefix(base) for page in report.pages]


# --- recursion ---------------------------------------------------------------


def test_depth_zero_fetches_the_seed_alone() -> None:
    with crawl(make_site()) as (report, base):
        assert visited_paths(report, base) == ["/"]
        assert report.state is CrawlState.COMPLETED


def test_depth_one_fetches_the_seed_and_what_it_links_to() -> None:
    with crawl(make_site(), max_depth=1) as (report, base):
        assert sorted(visited_paths(report, base)) == ["/", "/a", "/b"]


def test_depth_two_reaches_one_level_further() -> None:
    with crawl(make_site(), max_depth=2) as (report, base):
        assert sorted(visited_paths(report, base)) == ["/", "/a", "/a1", "/b", "/b1"]


def test_depth_three_reaches_the_whole_tree() -> None:
    with crawl(make_site(), max_depth=3) as (report, base):
        assert sorted(visited_paths(report, base)) == ["/", "/a", "/a1", "/a2", "/b", "/b1"]


def test_a_deeper_limit_than_the_site_still_terminates() -> None:
    with crawl(make_site(), max_depth=10) as (report, base):
        assert len(report.pages) == 6
        assert report.state is CrawlState.COMPLETED


def test_pages_are_fetched_breadth_first() -> None:
    with crawl(make_site(), max_depth=2) as (report, base):
        depths = [page.depth for page in report.pages]

    assert depths == sorted(depths)


def test_a_page_records_where_it_was_linked_from() -> None:
    with crawl(make_site(), max_depth=1) as (report, base):
        children = [page for page in report.pages if page.depth == 1]

    assert all(page.discovered_from == f"{base}/" for page in children)
    assert report.pages[0].discovered_from is None


# --- depth limiting ----------------------------------------------------------


def test_links_at_the_maximum_depth_are_discovered_but_not_followed() -> None:
    """Discovery counts them; the frontier never gets them."""
    with crawl(make_site(), max_depth=1) as (report, base):
        assert sorted(visited_paths(report, base)) == ["/", "/a", "/b"]
        assert report.links_discovered > len(report.pages)
        assert dict(report.statistics.skips_by_reason)[SkipReason.TOO_DEEP] > 0


def test_the_deepest_level_reached_is_reported() -> None:
    with crawl(make_site(), max_depth=2) as (report, base):
        assert report.statistics.max_depth_reached == 2


# --- scope -------------------------------------------------------------------


def test_an_external_link_is_followed_when_no_scope_is_set() -> None:
    """Hunting for share links is a first-class workflow, so this is default.

    "Elsewhere" is the same local server under its other hostname: 127.0.0.1
    and localhost are one machine but two hosts, which exercises the scope rule
    without the suite ever leaving this one.
    """
    site = Site()

    with serve(site) as base:
        elsewhere = f"http://localhost:{base.rsplit(':', 1)[1]}"
        site.add_html("/", f'<a href="{elsewhere}/away">away</a>')
        site.add_html("/away", "<p>elsewhere</p>")
        report = make_engine().run(make_session(f"{base}/", max_depth=1))

    assert dict(report.statistics.skips_by_reason).get(SkipReason.OUT_OF_SCOPE) is None
    assert [outcome.url for outcome in report.pages][1] == f"{elsewhere}/away"


def test_a_scope_refuses_the_same_machine_under_another_hostname() -> None:
    site = Site()

    with serve(site) as base:
        elsewhere = f"http://localhost:{base.rsplit(':', 1)[1]}"
        site.add_html("/", f'<a href="{elsewhere}/away">away</a>')
        site.add_html("/away", "<p>elsewhere</p>")
        engine = make_engine(policy=SameDomainPolicy(f"{base}/"))
        report = engine.run(make_session(f"{base}/", max_depth=1))

    assert len(report.pages) == 1
    assert dict(report.statistics.skips_by_reason)[SkipReason.OUT_OF_SCOPE] == 1


def test_a_scope_keeps_the_crawl_on_its_own_host() -> None:
    site = make_site({"/": f'<a href="/a">a</a><a href="{MEGA_LINK}">share</a>', "/a": "<p>x</p>"})

    with serve(site) as base:
        engine = make_engine(policy=SameDomainPolicy(f"{base}/"))
        report = engine.run(make_session(f"{base}/", max_depth=2))

    assert sorted(visited_paths(report, base)) == ["/", "/a"]
    assert dict(report.statistics.skips_by_reason)[SkipReason.OUT_OF_SCOPE] == 1


def test_a_mega_link_out_of_scope_is_still_discovered_and_classified() -> None:
    """Skipping a fetch must not skip the discovery."""
    site = make_site({"/": f'<a href="{MEGA_LINK}">share</a>'})

    with serve(site) as base:
        engine = make_engine(policy=SameDomainPolicy(f"{base}/"))
        report = engine.run(make_session(f"{base}/", max_depth=2))

    usage = {entry.name: entry.count for entry in report.summary.plugin_usage}
    assert usage["mega"] == 1


# --- duplicates and cycles ---------------------------------------------------


def test_a_cycle_terminates() -> None:
    site = make_site({"/": '<a href="/a">a</a>', "/a": '<a href="/">home</a>'})

    with crawl(site, max_depth=10) as (report, base):
        assert sorted(visited_paths(report, base)) == ["/", "/a"]
        assert report.state is CrawlState.COMPLETED


def test_a_self_link_terminates() -> None:
    site = make_site({"/": '<a href="/">itself</a>'})

    with crawl(site, max_depth=5) as (report, base):
        assert visited_paths(report, base) == ["/"]


def test_a_page_linked_from_many_pages_is_fetched_once() -> None:
    site = make_site(
        {
            "/": '<a href="/a">a</a><a href="/b">b</a>',
            "/a": '<a href="/shared">shared</a>',
            "/b": '<a href="/shared">shared</a>',
            "/shared": "<p>x</p>",
        }
    )

    with crawl(site, max_depth=3) as (report, base):
        assert visited_paths(report, base).count("/shared") == 1


def test_two_anchors_into_one_page_are_one_fetch() -> None:
    site = make_site({"/": '<a href="/a#intro">i</a><a href="/a#setup">s</a>', "/a": "<p>x</p>"})

    with crawl(site, max_depth=1) as (report, base):
        assert len(report.pages) == 2
        assert dict(report.statistics.skips_by_reason)[SkipReason.ALREADY_SEEN] == 1


def test_a_link_that_cannot_be_canonicalized_is_counted_as_unusable() -> None:
    engine = make_engine()
    engine._consider(CrawlItem(url="https://", depth=0))  # noqa: SLF001

    assert engine.frontier.pending == 0


# --- redirects ---------------------------------------------------------------


def test_a_redirect_target_linked_directly_is_not_fetched_twice() -> None:
    site = Site()
    site.add_html("/", '<a href="/old">old</a><a href="/new">new</a>')
    site.add("/old", status=302, location="/new", body=b"", content_type=None)
    site.add_html("/new", "<p>arrived</p>")

    with crawl(site, max_depth=2) as (report, base):
        fetched = [page.final_url for page in report.pages if page.succeeded]

    assert fetched.count(f"{base}/new") == 1


def test_a_redirect_records_both_urls() -> None:
    site = Site()
    site.add_html("/", '<a href="/old">old</a>')
    site.add("/old", status=302, location="/new", body=b"", content_type=None)
    site.add_html("/new", "<p>arrived</p>")

    with crawl(site, max_depth=1) as (report, base):
        moved = report.pages[1]

    assert moved.url == f"{base}/old"
    assert moved.final_url == f"{base}/new"
    assert moved.was_redirected is True


def test_a_redirect_loop_fails_one_page_without_stopping_the_crawl() -> None:
    site = Site()
    site.add_html("/", '<a href="/loop">loop</a><a href="/fine">fine</a>')
    site.add("/loop", status=302, location="/loop", body=b"", content_type=None)
    site.add_html("/fine", "<p>x</p>")

    with crawl(site, max_depth=1) as (report, base):
        assert report.statistics.pages_visited == 2
        assert report.statistics.pages_failed == 1
        assert report.state is CrawlState.COMPLETED


# --- failures ----------------------------------------------------------------


def test_one_missing_page_does_not_stop_the_crawl() -> None:
    site = make_site({"/": '<a href="/gone">gone</a><a href="/fine">fine</a>', "/fine": "<p>x</p>"})

    with crawl(site, max_depth=1) as (report, base):
        assert report.statistics.pages_failed == 1
        assert report.statistics.pages_visited == 2
        assert report.failures[0].error is not None


def test_a_link_whose_extension_gives_it_away_is_not_even_requested() -> None:
    site = make_site({"/": '<a href="/data.json">data</a>'})
    site.add("/data.json", body=b"{}", content_type="application/json")

    with crawl(site, max_depth=1) as (report, base):
        assert report.statistics.pages_failed == 0
        assert dict(report.statistics.skips_by_reason)[SkipReason.NOT_A_PAGE] == 1
        assert "/data.json" not in {request.path for request in site.requests}
        assert report.state is CrawlState.COMPLETED


def test_a_seed_that_cannot_be_read_stops_everything() -> None:
    site = Site()

    with serve(site) as base, pytest.raises(Exception, match="HTTP 404"):
        make_engine().run(make_session(f"{base}/missing"))


def test_a_seed_refused_by_the_policy_says_so() -> None:
    site = make_site()

    with serve(site) as base:
        engine = make_engine(policy=SameDomainPolicy("https://elsewhere.test/"))
        with pytest.raises(PolicyRefusedError, match="nothing to crawl"):
            engine.run(make_session(f"{base}/", max_depth=1))


# --- limits and termination --------------------------------------------------


def test_the_page_ceiling_stops_the_crawl_and_says_so() -> None:
    with crawl(make_site(), max_depth=5, max_pages=3) as (report, base):
        assert len(report.pages) == 3
        assert report.state is CrawlState.PAGE_LIMIT
        assert report.statistics.frontier_remaining > 0


def test_an_exhausted_frontier_completes_with_nothing_left() -> None:
    with crawl(make_site(), max_depth=5) as (report, base):
        assert report.state is CrawlState.COMPLETED
        assert report.statistics.frontier_remaining == 0
        assert report.was_complete is True


def test_a_requested_stop_ends_the_crawl_with_a_full_report() -> None:
    control = CrawlControl()
    site = make_site()

    class StopAfterFirst:
        """Presses the stop button once the first page is done."""

        def __init__(self) -> None:
            self.seen = 0

        def may_fetch(self, url: str) -> PolicyDecision:
            self.seen += 1
            if self.seen > 1:
                control.request_stop()
            return PolicyDecision.allow()

    with serve(site) as base:
        engine = make_engine(control=control, policy=StopAfterFirst())
        report = engine.run(make_session(f"{base}/", max_depth=5))

    assert report.state is CrawlState.INTERRUPTED
    assert control.state is CrawlState.INTERRUPTED
    assert report.statistics.pages_visited >= 1
    assert report.summary.documents_processed == report.statistics.pages_visited


def test_the_control_reports_the_terminal_state() -> None:
    control = CrawlControl()

    with crawl(make_site(), engine=make_engine(control=control)) as (report, base):
        pass

    assert control.state is CrawlState.COMPLETED


# --- statistics --------------------------------------------------------------


def test_pages_visited_equals_documents_processed() -> None:
    with crawl(make_site(), max_depth=2) as (report, base):
        assert report.statistics.pages_visited == report.summary.documents_processed


def test_every_skipped_url_is_counted_with_a_reason() -> None:
    with crawl(make_site(), max_depth=1) as (report, base):
        by_reason = dict(report.statistics.skips_by_reason)

    assert sum(by_reason.values()) == report.statistics.pages_skipped
    assert report.statistics.pages_skipped > 0


def test_the_crawl_reports_how_long_it_took() -> None:
    with crawl(make_site(), max_depth=1) as (report, base):
        assert report.statistics.elapsed_seconds >= 0.0


def test_the_report_names_the_seed_and_the_session() -> None:
    with crawl(make_site()) as (report, base):
        assert report.seed_url == f"{base}/"
        assert report.session.session_id == "crawl-1"


def test_the_discovery_session_is_opened_once_for_the_whole_crawl() -> None:
    from maxicrawler.events import ScanFinished, ScanStarted

    bus = EventBus()
    seen: list[object] = []
    for event_type in (ScanStarted, ScanFinished):
        bus.subscribe(event_type, seen.append)
    service = WebDiscoveryService(
        DiscoveryPipeline(bus),
        fetcher=UrllibPageFetcher(user_agent="MaxiCrawler/test", timeout=5.0),
    )

    with serve(make_site()) as base:
        CrawlEngine(service).run(make_session(f"{base}/", max_depth=2))

    assert [type(event) for event in seen] == [ScanStarted, ScanFinished]


# --- reuse and seams ---------------------------------------------------------


def test_a_second_run_reports_its_own_numbers() -> None:
    engine = make_engine()
    site = make_site()

    with serve(site) as base:
        first = engine.run(make_session(f"{base}/", max_depth=1))
        second = engine.run(make_session(f"{base}/a2", max_depth=0))

    assert len(first.pages) == 3
    assert len(second.pages) == 1


def test_a_second_run_still_remembers_what_was_already_fetched() -> None:
    """Counters reset between runs; identity deliberately does not.

    That is what lets several seeds be crawled without fetching a page they
    share, and it is why the visited set is injected rather than owned.
    """
    engine = make_engine()
    site = make_site()

    with serve(site) as base:
        engine.run(make_session(f"{base}/", max_depth=1))
        with pytest.raises(PolicyRefusedError, match="already seen"):
            engine.run(make_session(f"{base}/a", max_depth=0))


def test_a_supplied_frontier_and_visited_set_are_used() -> None:
    frontier = FifoFrontier()
    engine = make_engine(frontier=frontier)

    with crawl(make_site(), engine=engine, max_depth=1) as (report, base):
        assert engine.frontier is frontier
        assert visit_key(f"{base}/") in engine.visited


def test_a_visited_set_seeded_with_a_page_skips_it() -> None:
    from maxicrawler.web.frontier import InMemoryVisitedSet

    site = make_site({"/": '<a href="/a">a</a>', "/a": "<p>x</p>"})

    with serve(site) as base:
        visited = InMemoryVisitedSet([visit_key(f"{base}/a")])
        engine = make_engine(visited=visited)
        report = engine.run(make_session(f"{base}/", max_depth=1))

    assert visited_paths(report, base) == ["/"]


# --- events ------------------------------------------------------------------


def test_a_crawl_announces_its_start_and_its_end() -> None:
    from maxicrawler.events import CrawlFinished, CrawlStarted

    bus = EventBus()
    seen: list[object] = []
    bus.subscribe(CrawlStarted, seen.append)
    bus.subscribe(CrawlFinished, seen.append)

    with crawl(make_site(), engine=make_engine(event_bus=bus), max_depth=1) as (report, base):
        pass

    started, finished = seen
    assert isinstance(started, CrawlStarted)
    assert started.seed_url == f"{base}/"
    assert started.max_depth == 1
    assert isinstance(finished, CrawlFinished)
    assert finished.state == "completed"
    assert finished.pages_visited == 3


def test_every_read_page_is_announced() -> None:
    from maxicrawler.events import PageCrawled

    bus = EventBus()
    seen: list[PageCrawled] = []
    bus.subscribe(PageCrawled, seen.append)

    with crawl(make_site(), engine=make_engine(event_bus=bus), max_depth=1) as (report, base):
        pass

    assert len(seen) == 3
    assert {event.depth for event in seen} == {0, 1}
    assert all(event.status == 200 for event in seen)
    assert all(event.session_id == "crawl-1" for event in seen)


def test_a_failed_page_is_announced_with_its_reason() -> None:
    from maxicrawler.events import PageFailed

    bus = EventBus()
    seen: list[PageFailed] = []
    bus.subscribe(PageFailed, seen.append)
    site = make_site({"/": '<a href="/gone">gone</a>'})

    with crawl(site, engine=make_engine(event_bus=bus), max_depth=1) as (report, base):
        pass

    assert len(seen) == 1
    assert seen[0].url == f"{base}/gone"
    assert "404" in seen[0].reason


def test_an_interrupted_crawl_still_announces_its_end() -> None:
    from maxicrawler.events import CrawlFinished

    bus = EventBus()
    control = CrawlControl()
    seen: list[CrawlFinished] = []
    bus.subscribe(CrawlFinished, seen.append)

    class StopAfterFirst:
        def __init__(self) -> None:
            self.seen = 0

        def may_fetch(self, url: str) -> PolicyDecision:
            self.seen += 1
            if self.seen > 1:
                control.request_stop()
            return PolicyDecision.allow()

    with serve(make_site()) as base:
        engine = make_engine(event_bus=bus, control=control, policy=StopAfterFirst())
        engine.run(make_session(f"{base}/", max_depth=5))

    assert seen[0].state == "interrupted"


def test_a_crawl_without_a_bus_publishes_nothing_and_still_works() -> None:
    with crawl(make_site(), max_depth=1) as (report, base):
        assert report.state is CrawlState.COMPLETED


def test_the_domain_option_is_honoured_without_wiring_a_policy() -> None:
    """An option that only works when a caller also injects a policy is a trap.

    Wired that way, a report and a database row would claim the crawl stayed on
    one host while it happily wandered off it -- which is exactly what happened
    until the outbound-connection guard caught it.
    """
    site = Site()

    with serve(site) as base:
        elsewhere = f"http://localhost:{base.rsplit(':', 1)[1]}"
        site.add_html("/", f'<a href="{elsewhere}/away">away</a><a href="/a">a</a>')
        site.add_html("/away", "<p>elsewhere</p>")
        site.add_html("/a", "<p>x</p>")
        report = make_engine().run(make_session(f"{base}/", max_depth=1, same_domain=True))

    assert sorted(visited_paths(report, base)) == ["/", "/a"]
    assert dict(report.statistics.skips_by_reason)[SkipReason.OUT_OF_SCOPE] == 1


def test_an_injected_policy_is_asked_alongside_the_domain_option() -> None:
    class RefuseLeaves:
        def may_fetch(self, url: str) -> PolicyDecision:
            if url.endswith("/a1"):
                return PolicyDecision.refuse("no leaves")
            return PolicyDecision.allow()

    with serve(make_site()) as base:
        engine = make_engine(policy=RefuseLeaves())
        report = engine.run(make_session(f"{base}/", max_depth=3, same_domain=True))

    assert "/a1" not in visited_paths(report, base)
    assert "/b1" in visited_paths(report, base)


def test_a_refusal_is_counted_under_the_rule_that_refused_it() -> None:
    """The gate translates the decision rather than assuming what said no.

    Without this the report would file every refusal under "out of scope",
    which is the one thing robots.txt must not be confused with.
    """

    class RefuseLeaves:
        def may_fetch(self, url: str) -> PolicyDecision:
            if url.endswith("/a1"):
                return PolicyDecision.refuse("pretend robots", rule=PolicyRule.ROBOTS)
            return PolicyDecision.allow()

    with serve(make_site()) as base:
        engine = make_engine(policy=RefuseLeaves())
        report = engine.run(make_session(f"{base}/", max_depth=3))

    skips = dict(report.statistics.skips_by_reason)
    assert skips[SkipReason.ROBOTS_TXT] == 1
    assert SkipReason.OUT_OF_SCOPE not in skips


# --- the second gate, immediately before the request -------------------------


class RecordingGate:
    """A gate that refuses the paths it was given, and remembers every ask."""

    def __init__(self, *refused: str) -> None:
        self._refused = refused
        self.asked: list[str] = []

    def may_fetch(self, url: str) -> PolicyDecision:
        self.asked.append(url)
        if any(url.endswith(path) for path in self._refused):
            return PolicyDecision.refuse("pretend robots", rule=PolicyRule.ROBOTS)
        return PolicyDecision.allow()


def test_the_gate_refuses_a_page_and_the_crawl_carries_on() -> None:
    gate = RecordingGate("/a")

    with serve(make_site()) as base:
        report = make_engine(gate=gate).run(make_session(f"{base}/", max_depth=2))

    assert "/a" not in visited_paths(report, base)
    assert "/b" in visited_paths(report, base)
    assert dict(report.statistics.skips_by_reason)[SkipReason.ROBOTS_TXT] == 1


def test_the_gate_is_asked_only_about_urls_that_are_genuinely_next() -> None:
    """The whole reason the second gate exists.

    At the first gate, a policy that reads robots.txt would be asked about
    every URL a page links to — a page linking to three hundred domains would
    cost three hundred requests to then crawl a handful. Here it is asked once
    per page actually taken off the frontier, which is what `max_pages` bounds.
    """
    gate = RecordingGate()

    with serve(make_site()) as base:
        report = make_engine(gate=gate).run(make_session(f"{base}/", max_depth=1, max_pages=2))

    assert len(gate.asked) == len(report.pages) == 2


def test_a_page_the_gate_refuses_does_not_spend_the_page_ceiling() -> None:
    """It never became a request, and the ceiling counts requests."""
    gate = RecordingGate("/a")

    with serve(make_site()) as base:
        report = make_engine(gate=gate).run(make_session(f"{base}/", max_depth=1, max_pages=3))

    assert sorted(visited_paths(report, base)) == ["/", "/b"]
    assert report.statistics.pages_attempted == 2


def test_a_seed_the_gate_refuses_ends_the_crawl_with_its_rule() -> None:
    gate = RecordingGate("/")

    with serve(make_site()) as base, pytest.raises(PolicyRefusedError) as refusal:
        make_engine(gate=gate).run(make_session(f"{base}/"))

    assert refusal.value.rule is PolicyRule.ROBOTS
    assert "disallowed by robots.txt" in str(refusal.value)


def test_without_a_gate_nothing_is_asked_twice() -> None:
    """The default gate permits everything, so an unwired engine is unchanged."""
    with crawl(make_site(), max_depth=1) as (report, base):
        assert sorted(visited_paths(report, base)) == ["/", "/a", "/b"]


# --- what is worth following -------------------------------------------------


def test_stylesheets_scripts_and_images_are_found_but_never_fetched() -> None:
    """They are resources, not documents. Following one buys a wasted request.

    The acceptance run for this sprint fetched seven CSS, JS and icon files and
    reported them as failed pages, which is what prompted this.
    """
    site = Site()
    site.add_html(
        "/",
        '<link rel="stylesheet" href="/s.css"><script src="/a.js"></script>'
        '<img src="/i.png"><a href="/real">real</a>',
    )
    site.add_html("/real", "<p>x</p>")

    with crawl(site, max_depth=1) as (report, base):
        assert visited_paths(report, base) == ["/", "/real"]
        assert report.statistics.pages_failed == 0
        assert dict(report.statistics.skips_by_reason)[SkipReason.NOT_A_PAGE] == 3


def test_a_resource_link_still_reaches_the_pipeline() -> None:
    """Not following it must not mean not discovering it."""
    site = Site()
    site.add_html("/", '<link rel="stylesheet" href="/s.css"><img src="/i.png">')

    with crawl(site, max_depth=1) as (report, base):
        assert report.links_discovered == 2
        assert report.summary.unique_urls == 2
        assert dict(report.statistics.links_by_kind)[LinkKind.STYLESHEET] == 1


def test_frames_and_meta_refreshes_are_followed() -> None:
    site = Site()
    site.add_html("/", '<iframe src="/framed"></iframe>')
    site.add_html("/framed", '<meta http-equiv="refresh" content="0; url=/next">')
    site.add_html("/next", "<p>x</p>")

    with crawl(site, max_depth=2) as (report, base):
        assert sorted(visited_paths(report, base)) == ["/", "/framed", "/next"]


def test_a_url_written_in_prose_is_followed() -> None:
    site = Site()

    with serve(site) as base:
        site.add_html("/", f"<p>see {base}/mentioned for more</p>")
        site.add_html("/mentioned", "<p>x</p>")
        report = make_engine().run(make_session(f"{base}/", max_depth=1))

    assert sorted(visited_paths(report, base)) == ["/", "/mentioned"]


def test_a_link_to_a_file_is_never_requested() -> None:
    """The crawl that prompted this spent 44% of its budget on PDFs.

    Each one cost a round trip to be told what the URL already said. The link
    is discovered, classified and reported either way -- that happened when the
    page holding it was read, before any of this.
    """
    site = Site()
    site.add_html("/", '<a href="/sheet.pdf">sheet</a><a href="/real">real</a>')
    site.add("/sheet.pdf", body=b"%PDF-1.7", content_type="application/pdf")
    site.add_html("/real", "<p>x</p>")

    with crawl(site, max_depth=1) as (report, base):
        requested = {request.path for request in site.requests}

    assert "/sheet.pdf" not in requested
    assert visited_paths(report, base) == ["/", "/real"]
    assert report.statistics.pages_failed == 0
    assert dict(report.statistics.skips_by_reason)[SkipReason.NOT_A_PAGE] == 1


def test_a_file_link_is_still_discovered_and_classified() -> None:
    site = Site()
    site.add_html("/", '<a href="/sheet.pdf">sheet</a>')

    with crawl(site, max_depth=1) as (report, base):
        assert report.links_discovered == 1
        assert report.summary.unique_urls == 1
        assert report.summary.plugin_usage[0].name == "generic"


def test_a_seed_the_operator_named_is_always_attempted() -> None:
    """An explicit instruction outranks a heuristic.

    Being told what actually came back beats being told the URL looked wrong.
    """
    site = Site()
    site.add("/sheet.pdf", body=b"%PDF-1.7", content_type="application/pdf")

    with serve(site) as base, pytest.raises(ContentTypeError):
        make_engine().run(make_session(f"{base}/sheet.pdf"))

    assert [request.path for request in site.requests] == ["/sheet.pdf"]


def test_a_server_that_answers_with_something_else_is_a_skip_not_a_failure() -> None:
    """The residue the extension filter cannot judge from a URL alone.

    "/download" says nothing; the reply says application/pdf. That is a clear
    answer to "is this a page?", so it is counted as a skip -- but it did cost
    a request, and the ceiling has to know.
    """
    site = make_site({"/": '<a href="/download">get it</a><a href="/a">a</a>', "/a": "<p>x</p>"})
    site.add("/download", body=b"%PDF-1.7", content_type="application/pdf")

    with crawl(site, max_depth=1) as (report, base):
        assert report.statistics.pages_failed == 0
        assert report.statistics.pages_visited == 2
        assert report.statistics.pages_attempted == 3
        assert report.statistics.requests_without_a_page == 1
        assert dict(report.statistics.skips_by_reason)[SkipReason.NOT_A_PAGE] == 1
        assert "/download" in {request.path for request in site.requests}


def test_the_ceiling_counts_requests_rather_than_pages_read() -> None:
    """Otherwise a site of nothing but non-pages draws unbounded requests."""
    markup = "".join(f'<a href="/d{index}">d{index}</a>' for index in range(20))
    site = make_site({"/": markup})
    for index in range(20):
        site.add(f"/d{index}", body=b"%PDF-1.7", content_type="application/pdf")

    with crawl(site, max_depth=1, max_pages=5) as (report, base):
        assert report.state is CrawlState.PAGE_LIMIT
        assert report.statistics.pages_attempted == 5
        assert report.statistics.pages_visited == 1
        assert len(site.requests) == 5


def test_a_seed_that_is_not_a_page_still_raises() -> None:
    site = Site()
    site.add("/thing", body=b"%PDF-1.7", content_type="application/pdf")

    with serve(site) as base, pytest.raises(ContentTypeError):
        make_engine().run(make_session(f"{base}/thing"))


def test_attempts_equal_pages_when_every_answer_was_a_page() -> None:
    with crawl(make_site(), max_depth=1) as (report, base):
        statistics = report.statistics

    assert statistics.pages_attempted == statistics.pages_visited + statistics.pages_failed
    assert statistics.requests_without_a_page == 0


def test_a_second_run_resets_the_attempt_count() -> None:
    engine = make_engine()

    with serve(make_site()) as base:
        engine.run(make_session(f"{base}/", max_depth=1))
        second = engine.run(make_session(f"{base}/a2", max_depth=0))

    assert second.statistics.pages_attempted == 1
