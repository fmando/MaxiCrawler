"""Tests for crawls running on a worker thread."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from time import monotonic, sleep

import pytest
from web_server import Site, serve

from maxicrawler.api.jobs import CrawlJob, CrawlJobs, JobSnapshot
from maxicrawler.app import CrawlService
from maxicrawler.config import Settings
from maxicrawler.events import CrawlFinished
from maxicrawler.web.session import CrawlOptions, CrawlSession, CrawlState

TREE = {
    "/": '<a href="/a">a</a><a href="/b">b</a>',
    "/a": '<a href="/a1">a1</a>',
    "/b": "<p>leaf</p>",
    "/a1": "<p>leaf</p>",
}


def make_site(pages: dict[str, str] | None = None) -> Site:
    """Return a local site to crawl."""
    site = Site()
    for path, markup in (pages if pages is not None else TREE).items():
        site.add_html(path, markup)
    return site


def make_service() -> CrawlService:
    """Return a service over throwaway settings."""
    return CrawlService(
        Settings(
            user_agent="MaxiCrawler/test",
            network_timeout=5.0,
            # The site these crawls reach is on loopback, which the shipped
            # default refuses. Stated here rather than turned off globally.
            allow_private_networks=True,
        )
    )


@contextmanager
def registry(**kwargs: object) -> Iterator[CrawlJobs]:
    """Yield a registry and shut its worker pool down afterwards."""
    jobs = CrawlJobs(make_service(), persist=False, **kwargs)  # type: ignore[arg-type]
    try:
        yield jobs
    finally:
        jobs.shutdown()


def wait_for(job: CrawlJob, *, timeout: float = 20.0) -> JobSnapshot:
    """Return the job's snapshot once it has finished."""
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        snapshot = job.snapshot()
        if snapshot.is_finished:
            return snapshot
        sleep(0.02)
    raise AssertionError(f"the crawl did not finish within {timeout}s")


# --- running -----------------------------------------------------------------


def test_a_submitted_crawl_runs_and_reports() -> None:
    with registry() as jobs, serve(make_site()) as base:
        session = jobs.service.build_session(f"{base}/", depth=1, same_domain=True)

        job = jobs.submit(session)
        snapshot = wait_for(job)

    assert snapshot.state is CrawlState.COMPLETED
    assert snapshot.pages_visited == 3
    assert job.report is not None
    assert job.report.pages_visited == 3


def test_submitting_returns_before_the_crawl_finishes() -> None:
    """The whole point: a request must not wait for a crawl."""
    with registry() as jobs, serve(make_site()) as base:
        session = jobs.service.build_session(f"{base}/", depth=3, same_domain=True)

        job = jobs.submit(session)
        immediately = job.snapshot()

        assert immediately.state in {CrawlState.PENDING, CrawlState.RUNNING}
        assert immediately.is_finished is False
        wait_for(job)


def test_a_job_is_addressed_by_its_session_identifier() -> None:
    with registry() as jobs, serve(make_site()) as base:
        session = jobs.service.build_session(f"{base}/", same_domain=True)

        job = jobs.submit(session)

        assert job.id == session.session_id
        assert jobs.get(session.session_id) is job
        wait_for(job)


def test_an_unknown_job_is_reported_as_missing() -> None:
    with registry() as jobs:
        assert jobs.get("nope") is None


# --- the snapshot ------------------------------------------------------------


def test_a_snapshot_counts_pages_and_links() -> None:
    with registry() as jobs, serve(make_site()) as base:
        job = jobs.submit(jobs.service.build_session(f"{base}/", depth=2, same_domain=True))
        snapshot = wait_for(job)

    assert snapshot.pages_visited == 4
    assert snapshot.links_found == 3
    assert snapshot.latest_url is not None


def test_a_snapshot_counts_a_failed_page_separately() -> None:
    site = make_site({"/": '<a href="/gone">gone</a><a href="/a">a</a>', "/a": "<p>x</p>"})

    with registry() as jobs, serve(site) as base:
        job = jobs.submit(jobs.service.build_session(f"{base}/", depth=1, same_domain=True))
        snapshot = wait_for(job)

    assert snapshot.pages_visited == 2
    assert snapshot.pages_failed == 1
    assert snapshot.pages_attempted == 3


def test_elapsed_time_advances_while_a_crawl_runs() -> None:
    """Derived on demand, so it is never stale between events."""
    with registry() as jobs, serve(make_site()) as base:
        job = jobs.submit(jobs.service.build_session(f"{base}/", depth=3, same_domain=True))
        first = job.snapshot().elapsed_seconds
        sleep(0.05)
        second = job.snapshot().elapsed_seconds
        wait_for(job)

    assert second > first


def test_elapsed_time_stops_when_the_crawl_does() -> None:
    with registry() as jobs, serve(make_site()) as base:
        job = jobs.submit(jobs.service.build_session(f"{base}/", same_domain=True))
        wait_for(job)
        settled = job.snapshot().elapsed_seconds
        sleep(0.05)

    assert job.snapshot().elapsed_seconds == pytest.approx(settled)


def test_progress_is_measured_against_the_page_ceiling() -> None:
    snapshot = JobSnapshot(
        job_id="j",
        seed_url="https://example.test/",
        state=CrawlState.RUNNING,
        options=CrawlOptions(max_pages=10),
        started_at=datetime.now(UTC),
        pages_visited=3,
    )

    assert snapshot.progress == pytest.approx(0.3)


def test_progress_is_complete_once_the_crawl_is() -> None:
    with registry() as jobs, serve(make_site()) as base:
        job = jobs.submit(jobs.service.build_session(f"{base}/", depth=3, same_domain=True))
        wait_for(job)

    assert job.snapshot().progress == 1.0


def test_progress_never_exceeds_one() -> None:
    snapshot = JobSnapshot(
        job_id="j",
        seed_url="https://example.test/",
        state=CrawlState.RUNNING,
        options=CrawlOptions(max_pages=2),
        started_at=datetime.now(UTC),
        pages_visited=9,
    )

    assert snapshot.progress == 1.0


# --- stopping and failing ----------------------------------------------------


def test_a_job_can_be_stopped() -> None:
    """The Stop button, using the same control Ctrl-C uses."""
    with registry() as jobs, serve(make_site()) as base:
        job = jobs.submit(jobs.service.build_session(f"{base}/", depth=5, same_domain=True))
        job.stop()
        snapshot = wait_for(job)

    assert snapshot.state is CrawlState.INTERRUPTED


def test_a_seed_that_cannot_be_read_becomes_a_failed_job_not_an_exception() -> None:
    with registry() as jobs, serve(Site()) as base:
        job = jobs.submit(jobs.service.build_session(f"{base}/nope"))
        snapshot = wait_for(job)

    assert snapshot.error is not None
    assert "404" in snapshot.error
    assert snapshot.is_finished is True
    assert job.report is None


def test_a_bug_in_a_crawl_still_finishes_the_job() -> None:
    """A worker that dies silently would leave a job saying "pending" forever."""

    class Exploding(CrawlService):
        def run(self, session: CrawlSession, **kwargs: object) -> object:  # type: ignore[override]
            msg = "something unforeseen"
            raise RuntimeError(msg)

    jobs = CrawlJobs(Exploding(Settings(user_agent="MaxiCrawler/test")), persist=False)
    try:
        session = jobs.service.build_session("https://example.test/")
        job = jobs.submit(session)
        snapshot = wait_for(job)
    finally:
        jobs.shutdown()

    assert snapshot.error == "RuntimeError: something unforeseen"


# --- the registry ------------------------------------------------------------


def test_recent_jobs_come_back_newest_first() -> None:
    with registry() as jobs, serve(make_site()) as base:
        first = jobs.submit(jobs.service.build_session(f"{base}/", same_domain=True))
        second = jobs.submit(jobs.service.build_session(f"{base}/a", same_domain=True))
        wait_for(first)
        wait_for(second)

        assert [job.id for job in jobs.recent()] == [second.id, first.id]


def test_recent_jobs_can_be_limited() -> None:
    with registry() as jobs, serve(make_site()) as base:
        for path in ("/", "/a", "/b"):
            wait_for(jobs.submit(jobs.service.build_session(f"{base}{path}", same_domain=True)))

        assert len(jobs.recent(limit=2)) == 2


def test_finished_jobs_are_evicted_beyond_the_limit() -> None:
    with registry(retain=2) as jobs, serve(make_site()) as base:
        submitted = []
        for path in ("/", "/a", "/b", "/a1"):
            job = jobs.submit(jobs.service.build_session(f"{base}{path}", same_domain=True))
            wait_for(job)
            submitted.append(job)

        assert jobs.get(submitted[0].id) is None
        assert jobs.get(submitted[-1].id) is not None


def test_shutting_down_asks_every_crawl_to_stop() -> None:
    jobs = CrawlJobs(make_service(), persist=False)

    with serve(make_site()) as base:
        job = jobs.submit(jobs.service.build_session(f"{base}/", depth=5, same_domain=True))
        jobs.shutdown()

    assert job.control.stop_requested is True


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [({"workers": 0}, "workers must be at least 1"), ({"retain": 0}, "retain must be at least 1")],
)
def test_an_impossible_registry_is_refused(kwargs: dict[str, int], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        CrawlJobs(make_service(), **kwargs)  # type: ignore[arg-type]


def test_two_crawls_do_not_share_a_pipeline() -> None:
    """Nothing is shared between jobs, which is why one worker is a setting."""
    with registry(workers=2) as jobs, serve(make_site()) as base:
        first = jobs.submit(jobs.service.build_session(f"{base}/", depth=1, same_domain=True))
        second = jobs.submit(jobs.service.build_session(f"{base}/a", depth=1, same_domain=True))
        wait_for(first)
        wait_for(second)

    assert first.bus is not second.bus
    assert first.control is not second.control
    assert first.snapshot().pages_visited == 3
    assert second.snapshot().pages_visited == 2


# --- finished means the report is there ---------------------------------------


def a_job() -> CrawlJob:
    """Return one job over a session nobody is going to run."""
    return CrawlJob(make_service().build_session("https://example.test/"))


def crawl_finished(job: CrawlJob) -> None:
    """Publish what the engine publishes when it stops crawling."""
    job.bus.publish(
        CrawlFinished(
            session_id=job.id, state=str(CrawlState.COMPLETED), pages_visited=1, pages_failed=0
        )
    )


def test_a_job_never_says_finished_before_its_report_is_there() -> None:
    """The event says the crawling stopped, not that the job is done.

    The engine writes the report to the repository *after* publishing this, so
    a job that went terminal here would be telling every reader to come and get
    a report that does not exist for as long as that write takes. What that
    looks like from the outside is a finished crawl whose page has no link
    table, and it is what made a dozen report tests fail on a loaded machine.
    """
    job = a_job()

    crawl_finished(job)

    assert job.report is None
    assert job.snapshot().is_finished is False


def test_the_clock_stops_when_the_crawling_does() -> None:
    """The one thing the event does settle: elapsed is about the crawling."""
    job = a_job()

    crawl_finished(job)
    settled = job.snapshot().elapsed_seconds
    sleep(0.05)

    assert job.snapshot().elapsed_seconds == pytest.approx(settled)


def test_a_crawl_that_will_never_report_is_finished_all_the_same() -> None:
    """Otherwise a seed that cannot be read leaves a job pending forever."""
    job = a_job()

    crawl_finished(job)
    job.fail("the seed could not be read")

    assert job.report is None
    assert job.snapshot().is_finished is True
