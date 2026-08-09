"""Crawls running somewhere else, and what is known about them while they do.

A crawl blocks. :meth:`~maxicrawler.web.engine.CrawlEngine.run` is synchronous
by design, and calling it from a request handler would stop the server
answering anything at all until it finished. So it runs on a worker thread, and
this module is what a request handler talks to instead.

Three decisions shape it.

**One worker by default.** That single number does three jobs at once: it bounds
SQLite write contention, it keeps the machine polite without a scheduler, and it
makes "two crawls at once" a deliberate setting rather than an accident.

**A fresh object graph per crawl**, which
:class:`~maxicrawler.app.CrawlService` already guarantees. Nothing is shared
between jobs, so :class:`~maxicrawler.crawler.DiscoveryPipeline` not being
thread-safe never becomes a problem here.

**The registry is a live view, not the record.** Jobs die with the process;
``crawl_sessions`` is what survives it. Finished jobs are evicted beyond a
limit so a long-running server does not accumulate reports forever.
"""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from time import monotonic

from maxicrawler.app import CrawlService
from maxicrawler.events import (
    CrawlFinished,
    CrawlStarted,
    EventBus,
    PageCrawled,
    PageFailed,
)
from maxicrawler.events.types import Event
from maxicrawler.web.errors import CrawlError
from maxicrawler.web.report import CrawlReport
from maxicrawler.web.session import CrawlControl, CrawlOptions, CrawlSession, CrawlState

DEFAULT_WORKERS = 1
"""How many crawls may run at once."""

DEFAULT_RETAINED_JOBS = 50
"""How many finished crawls the registry keeps before evicting the oldest."""


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    """What is true about one crawl at the moment it was asked.

    Immutable and derived: the job holds a handful of counters under a lock and
    builds one of these on demand, so the elapsed time is never stale between
    events.
    """

    job_id: str
    seed_url: str
    state: CrawlState
    options: CrawlOptions
    started_at: datetime
    pages_visited: int = 0
    pages_failed: int = 0
    links_found: int = 0
    elapsed_seconds: float = 0.0
    latest_url: str | None = None
    """The page that finished most recently.

    Not the page being fetched right now: ``PageCrawled`` fires once a page has
    been read, so the one in flight is by definition not in any event yet.
    Naming it for what it is keeps the interface from claiming otherwise.
    """

    error: str | None = None
    """Why the crawl could not run, when it could not."""

    @property
    def pages_attempted(self) -> int:
        """Return how many pages were read or tried."""
        return self.pages_visited + self.pages_failed

    @property
    def is_finished(self) -> bool:
        """Return whether the crawl has reached a terminal state."""
        return self.state.is_finished or self.error is not None

    @property
    def progress(self) -> float:
        """Return how far along the crawl is, between 0 and 1.

        Measured against the page ceiling, which is the only bound known in
        advance — a crawl that runs out of links finishes early, and the bar
        jumping to full at the end is the honest way to show that.
        """
        if self.is_finished:
            return 1.0
        return min(1.0, self.pages_attempted / self.options.max_pages)


class CrawlJob:
    """One crawl on a worker thread, and the handle a request holds on it."""

    def __init__(self, session: CrawlSession) -> None:
        self.session = session
        self.control = CrawlControl()
        self.bus = EventBus()
        self._lock = Lock()
        self._started = monotonic()
        self._finished: float | None = None
        self._pages_visited = 0
        self._pages_failed = 0
        self._links_found = 0
        self._latest_url: str | None = None
        self._state = CrawlState.PENDING
        self._error: str | None = None
        self._report: CrawlReport | None = None
        self._subscribe()

    @property
    def id(self) -> str:
        """Return the identifier this crawl is addressed by."""
        return self.session.session_id

    @property
    def report(self) -> CrawlReport | None:
        """Return the finished report, or ``None`` while it is still running."""
        with self._lock:
            return self._report

    def snapshot(self) -> JobSnapshot:
        """Return what is true about this crawl right now."""
        with self._lock:
            elapsed = (
                self._finished if self._finished is not None else monotonic()
            ) - self._started
            return JobSnapshot(
                job_id=self.session.session_id,
                seed_url=self.session.seed_url,
                state=self._state,
                options=self.session.options,
                started_at=self.session.started_at,
                pages_visited=self._pages_visited,
                pages_failed=self._pages_failed,
                links_found=self._links_found,
                elapsed_seconds=elapsed,
                latest_url=self._latest_url,
                error=self._error,
            )

    def stop(self) -> None:
        """Ask the crawl to stop after the page it is working on.

        Not instant, and the interface should not pretend otherwise: the engine
        checks between pages, so a stop during a slow fetch waits for that
        fetch's timeout.
        """
        self.control.request_stop()

    def complete(self, report: CrawlReport) -> None:
        """Record the report a finished crawl produced.

        Called by the registry that owns this job, on the worker thread.
        """
        with self._lock:
            self._report = report
            self._state = report.state
            if self._finished is None:
                self._finished = monotonic()

    def fail(self, reason: str) -> None:
        """Record that the crawl never produced a report.

        A seed that cannot be read raises instead of returning one, and a job
        left reporting ``pending`` forever would be a worse answer than an
        error the interface can show.
        """
        with self._lock:
            self._error = reason
            self._finished = monotonic()

    # --- what the crawl reports as it runs -----------------------------------

    def _subscribe(self) -> None:
        """Listen to the crawl this job will run."""
        self.bus.subscribe(CrawlStarted, self._on_started)
        self.bus.subscribe(PageCrawled, self._on_page_crawled)
        self.bus.subscribe(PageFailed, self._on_page_failed)
        self.bus.subscribe(CrawlFinished, self._on_finished)

    def _on_started(self, event: Event) -> None:
        """Note that the crawl has begun."""
        with self._lock:
            self._state = CrawlState.RUNNING

    def _on_page_crawled(self, event: Event) -> None:
        """Count one page that was read.

        The bus dispatches on the concrete type, so this cannot be anything
        else; the check is what tells the type checker so, and it costs a
        comparison per page.
        """
        if not isinstance(event, PageCrawled):
            return
        with self._lock:
            self._pages_visited += 1
            self._links_found += event.link_count
            self._latest_url = event.final_url

    def _on_page_failed(self, event: Event) -> None:
        """Count one page that could not be read."""
        if not isinstance(event, PageFailed):
            return
        with self._lock:
            self._pages_failed += 1
            self._latest_url = event.url

    def _on_finished(self, event: Event) -> None:
        """Note how the crawl ended."""
        if not isinstance(event, CrawlFinished):
            return
        with self._lock:
            self._state = CrawlState(event.state)
            self._finished = monotonic()


class CrawlJobs:
    """The crawls this process knows about."""

    def __init__(
        self,
        service: CrawlService,
        *,
        workers: int = DEFAULT_WORKERS,
        retain: int = DEFAULT_RETAINED_JOBS,
        persist: bool = True,
    ) -> None:
        if workers < 1:
            msg = "workers must be at least 1"
            raise ValueError(msg)
        if retain < 1:
            msg = "retain must be at least 1"
            raise ValueError(msg)
        self._service = service
        self._persist = persist
        self._retain = retain
        self._lock = Lock()
        self._jobs: OrderedDict[str, CrawlJob] = OrderedDict()
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="maxicrawl")

    @property
    def service(self) -> CrawlService:
        """Return the service every job of this registry runs through."""
        return self._service

    def submit(self, session: CrawlSession) -> CrawlJob:
        """Start crawling *session* on a worker thread and return its job."""
        job = CrawlJob(session)
        with self._lock:
            self._jobs[job.id] = job
            self._evict()
        self._executor.submit(self._run, job)
        return job

    def get(self, job_id: str) -> CrawlJob | None:
        """Return the job called *job_id*, if this process still holds it."""
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 20) -> tuple[CrawlJob, ...]:
        """Return the most recently submitted jobs, newest first."""
        with self._lock:
            newest_first = tuple(reversed(self._jobs.values()))
        return newest_first[:limit]

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop accepting work, asking every running crawl to stop first."""
        with self._lock:
            jobs = tuple(self._jobs.values())
        for job in jobs:
            job.stop()
        self._executor.shutdown(wait=wait)

    def _run(self, job: CrawlJob) -> None:
        """Run one crawl to its end, whatever that end turns out to be."""
        try:
            report = self._service.run(
                job.session,
                persist=self._persist,
                control=job.control,
                event_bus=job.bus,
            )
        except CrawlError as error:
            job.fail(str(error))
        except Exception as error:  # noqa: BLE001
            # Anything else is a bug on our side, and a job left saying
            # "pending" forever would hide it. Record it where it can be seen.
            job.fail(f"{type(error).__name__}: {error}")
        else:
            job.complete(report)

    def _evict(self) -> None:
        """Drop the oldest finished jobs once there are more than we keep.

        Callers hold the lock. A running job is never evicted however old it
        is, because the registry is how a request finds it again.
        """
        finished = [job_id for job_id, job in self._jobs.items() if job.snapshot().is_finished]
        for job_id in finished[: max(0, len(finished) - self._retain)]:
            del self._jobs[job_id]
