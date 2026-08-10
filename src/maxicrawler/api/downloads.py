"""Downloads running somewhere else, and what is known about them while they do.

A transfer blocks for as long as the file takes, so it runs on a worker thread
and a request handler talks to this module instead. That is the same shape
:mod:`maxicrawler.api.jobs` gives a crawl (ADR-024), deliberately: two
background things in one server should not have two designs.

Three decisions shape it, and only the first differs from crawls.

**One at a time, and no queue.** A second request while a transfer is running is
refused with :class:`~maxicrawler.api.errors.DownloadBusyError` rather than
queued. A queue needs a policy for ordering, cancelling, resuming and surviving
a restart; none of that is worth inventing before one download works end to end.

**The registry is a live view, not the record.** Runs die with the process. What
survives it is the library, which is where a finished download actually lives —
so a run this process no longer holds is not a loss, it is a page that points at
the library instead.

**The credential never lands here.** A share link carries its decryption key in
the URL fragment. It arrives in a form body, is handed to the service as an
argument, and is held by nothing: a :class:`DownloadRun` knows only the
fragment-free URL, which is what every snapshot, every page and every event
frame is built from.
"""

from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from time import monotonic
from uuid import uuid4

from maxicrawler.api.errors import DownloadBusyError
from maxicrawler.app import (
    DownloadControl,
    DownloadProgress,
    DownloadService,
    DownloadSummary,
)
from maxicrawler.domain import DownloadStatus
from maxicrawler.utils import strip_fragment

DEFAULT_RETAINED_RUNS = 20
"""How many finished downloads the registry keeps before evicting the oldest."""

BUSY_MESSAGE = "a download is already running; this interface runs one at a time"
"""Said to whoever asked for a second one, on the page they asked from."""


@dataclass(frozen=True, slots=True)
class DownloadSnapshot:
    """What is true about one download at the moment it was asked.

    Immutable and derived, like :class:`~maxicrawler.api.jobs.JobSnapshot`: the
    run holds the latest progress under a lock and builds one of these on
    demand, so the elapsed time is never stale between frames.
    """

    download_id: str
    url: str
    """The share link without its fragment, and therefore without its key."""

    progress: DownloadProgress
    started_at: datetime
    elapsed_seconds: float = 0.0
    summary: DownloadSummary | None = None
    """Present once the download is over, whatever way it ended."""

    error: str | None = None
    """Why the download never produced a summary, when it did not."""

    @property
    def is_finished(self) -> bool:
        """Return whether this download has reached a terminal state."""
        return self.summary is not None or self.error is not None

    @property
    def status(self) -> DownloadStatus:
        """Return the verdict if there is one, and the running state if not."""
        if self.summary is not None:
            return self.summary.status
        return DownloadStatus.FAILED if self.error is not None else self.progress.status

    @property
    def label(self) -> str:
        """Return what to call the resource being fetched."""
        if self.summary is not None:
            return self.summary.label
        return self.progress.label

    @property
    def path(self) -> Path | None:
        """Return where the payload landed, once one has."""
        return None if self.summary is None else self.summary.path

    @property
    def reason(self) -> str | None:
        """Return the one line explaining anything but a plain success."""
        if self.error is not None:
            return self.error
        if self.summary is not None:
            return self.summary.reason
        return self.progress.reason


class DownloadRun:
    """One transfer on a worker thread, and the handle a request holds on it."""

    def __init__(self, download_id: str, url: str) -> None:
        self._id = download_id
        self._url = url
        self.control = DownloadControl()
        self._lock = Lock()
        self._started = monotonic()
        self._started_at = datetime.now(UTC)
        self._finished: float | None = None
        self._progress = DownloadProgress(label=url, status=DownloadStatus.PENDING, files_total=0)
        self._summary: DownloadSummary | None = None
        self._error: str | None = None
        self._listeners: list[Callable[[DownloadSnapshot], None]] = []

    @property
    def id(self) -> str:
        """Return the identifier this download is addressed by."""
        return self._id

    @property
    def url(self) -> str:
        """Return the share link without its fragment."""
        return self._url

    @property
    def summary(self) -> DownloadSummary | None:
        """Return the account of a finished download, or ``None`` while it runs."""
        with self._lock:
            return self._summary

    def snapshot(self) -> DownloadSnapshot:
        """Return what is true about this download right now."""
        with self._lock:
            elapsed = (
                self._finished if self._finished is not None else monotonic()
            ) - self._started
            return DownloadSnapshot(
                download_id=self._id,
                url=self._url,
                progress=self._progress,
                started_at=self._started_at,
                elapsed_seconds=elapsed,
                summary=self._summary,
                error=self._error,
            )

    def add_listener(self, listener: Callable[[DownloadSnapshot], None]) -> None:
        """Call *listener* with a fresh snapshot after every change.

        Listeners run **on the worker thread**, inside the transfer, and are
        called once per written chunk. They must do as little as possible and
        must never block: the one in :mod:`maxicrawler.api.stream` hands the
        snapshot to an event loop and returns.
        """
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[DownloadSnapshot], None]) -> None:
        """Stop calling *listener*; unknown listeners are ignored."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    def stop(self) -> None:
        """Ask this transfer to stop.

        Takes effect within one chunk rather than at the end of the file, and
        leaves the library exactly as it was — the staging directory means a
        transfer that does not finish was never visible in the first place.

        The same shape as :meth:`~maxicrawler.api.jobs.CrawlJob.stop`, because
        a person clicking Stop should not have to learn which half of the chain
        they are looking at.
        """
        self.control.request_stop()

    def report_progress(self, progress: DownloadProgress) -> None:
        """Record what the service reports, and tell whoever is watching."""
        with self._lock:
            self._progress = progress
        self._announce()

    def complete(self, summary: DownloadSummary) -> None:
        """Record the account a finished download produced."""
        with self._lock:
            self._summary = summary
            if self._finished is None:
                self._finished = monotonic()
        self._announce()

    def fail(self, reason: str) -> None:
        """Record that the download never produced a summary.

        Not the same thing as a failed transfer, which *is* a summary with a
        reason. This is for the case where something below broke badly enough to
        raise — a library that cannot be written, a bug on our side — and a page
        left saying "downloading" forever would hide it.
        """
        with self._lock:
            self._error = reason
            self._finished = monotonic()
        self._announce()

    def _announce(self) -> None:
        """Hand a fresh snapshot to every listener.

        Deliberately outside the lock. Calling into somebody else's code while
        holding a lock is how a slow listener becomes a stalled transfer.
        """
        if not self._listeners:
            return
        snapshot = self.snapshot()
        for listener in tuple(self._listeners):
            listener(snapshot)


class DownloadRuns:
    """The downloads this process knows about, one running at a time."""

    def __init__(self, service: DownloadService, *, retain: int = DEFAULT_RETAINED_RUNS) -> None:
        if retain < 1:
            msg = "retain must be at least 1"
            raise ValueError(msg)
        self._service = service
        self._retain = retain
        self._lock = Lock()
        self._runs: OrderedDict[str, DownloadRun] = OrderedDict()
        self._active: DownloadRun | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="maxidownload")

    @property
    def service(self) -> DownloadService:
        """Return the service every download of this registry goes through."""
        return self._service

    def submit(self, url: str) -> DownloadRun:
        """Start downloading *url* on the worker thread and return its run.

        Raises:
            ValueError: *url* is not an absolute HTTP(S) URL. Checked before a
                thread is started, so a bad link is a message beside the button
                rather than a run that exists only to have failed.
            DownloadBusyError: something is already downloading.
        """
        target = self._service.require_url(url)
        run = DownloadRun(uuid4().hex, strip_fragment(target))
        with self._lock:
            if self._active is not None:
                raise DownloadBusyError(BUSY_MESSAGE)
            self._active = run
            self._runs[run.id] = run
            self._evict()
        # The URL with its fragment goes to the worker as an argument and is
        # held by nothing else. The run itself never learns the key.
        self._executor.submit(self._run, run, target)
        return run

    def get(self, download_id: str) -> DownloadRun | None:
        """Return the run called *download_id*, if this process still holds it."""
        with self._lock:
            return self._runs.get(download_id)

    def active(self) -> DownloadRun | None:
        """Return the download running right now, if there is one."""
        with self._lock:
            return self._active

    def recent(self, limit: int = DEFAULT_RETAINED_RUNS) -> tuple[DownloadRun, ...]:
        """Return the most recently submitted runs, newest first."""
        with self._lock:
            newest_first = tuple(reversed(self._runs.values()))
        return newest_first[:limit]

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop accepting downloads, asking a running transfer to stop first.

        Before this there was no cooperative stop, so a server shutting down
        left a transfer running until its file was done — on a large file, a
        shutdown that looked like a hang. Now it ends at the next chunk, and
        the library is left as it was.
        """
        with self._lock:
            active = self._active
        if active is not None:
            active.stop()
        self._executor.shutdown(wait=wait)

    def _run(self, run: DownloadRun, url: str) -> None:
        """Run one download to its end, whatever that end turns out to be."""
        try:
            summary = self._service.download(
                url, on_progress=run.report_progress, control=run.control
            )
        except Exception as error:  # noqa: BLE001
            # A transfer that fails is a summary, not an exception, so anything
            # arriving here is a fault below us. A run left saying "downloading"
            # forever would hide it; record it where it can be seen.
            run.fail(f"{type(error).__name__}: {error}")
        else:
            run.complete(summary)
        finally:
            with self._lock:
                self._active = None

    def _evict(self) -> None:
        """Drop the oldest finished runs once there are more than we keep.

        Callers hold the lock. A running download is never evicted, because the
        registry is how a request finds it again.
        """
        finished = [key for key, run in self._runs.items() if run.snapshot().is_finished]
        for key in finished[: max(0, len(finished) - self._retain)]:
            del self._runs[key]
