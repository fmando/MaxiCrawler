"""Downloads waiting, running, and what is known about them while they do.

A transfer blocks for as long as the file takes, so it runs on a worker thread
and a request handler talks to this module instead. That is the same shape
:mod:`maxicrawler.api.jobs` gives a crawl (ADR-024), deliberately: two
background things in one server should not have two designs.

**There are two queues in this project, one above the other, and they are named
apart on purpose.** The one below — ``maxicrawler.downloader.queue``'s
``DownloadQueue`` — holds the *jobs of one plan*: the files a single share link
turned out to contain, already resolved, already addressed by provider and
identity. :class:`TransferQueue` here holds *requests*: URLs nobody has planned
yet, which may each turn out to be one file or two hundred.

They are not merged because they answer different questions, and this package
may not import that one at all — a boundary ``tests/test_api_boundaries.py``
reads rather than believes. That test is also why the names differ: it forbids
`api` from naming the download layer's builders and matches on the class name
alone, so two classes called ``DownloadQueue`` would make a real rule
unenforceable to save one word.

Four decisions shape this module.

**Order, and one worker.** Requests are drained in the order they arrived, by a
single long-lived thread. One worker is not a placeholder for a thread pool: how
many transfers a host should face at once is a politeness question, and this
sprint's subject is a person's workflow rather than a host's patience.

**Nothing here executes a download.**
:meth:`~maxicrawler.app.DownloadService.download` does, unchanged. This module
decides *order* and *when* — which item is next, whether the worker may take it,
and what a cancelled one becomes. Every transfer that happens goes through the
one service call, so there is exactly one place downloads start (ADR-033).

**The registry is a live view, not the record.** Runs die with the process. What
survives is the library, which is where a finished download actually lives — so
a run this process no longer holds is not a loss, it is a page that points at
the library instead. A queue that survived a restart is a different feature with
a different question (what does a half-finished transfer resume from?) and is
deliberately not this one.

**The credential is held, and confined.** A share link carries its decryption
key in the URL fragment, and a queued request has to keep it: the transfer that
will need it has not started yet, and a retry may need it again later. It lives
in one private dictionary on the queue, is evicted with its run, and reaches
nothing else — a :class:`DownloadRun` still knows only the fragment-free URL,
which is what every snapshot, every page and every event frame is built from.
``tests/test_api_secret_confinement.py`` asserts that rather than trusting it.
This is a smaller exposure than it sounds: the same URL, fragment included, is
already written to SQLite by discovery and rendered into the report's table.
"""

from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import Condition, Lock, Thread
from time import monotonic
from uuid import uuid4

from maxicrawler.api.errors import QueueFullError
from maxicrawler.app import (
    DownloadControl,
    DownloadProgress,
    DownloadService,
    DownloadSummary,
)
from maxicrawler.domain import DownloadStatus
from maxicrawler.utils import strip_fragment

DEFAULT_RETAINED_RUNS = 50
"""How many finished downloads the queue keeps before evicting the oldest.

Larger than the twenty it was when downloads happened one at a time: a queue of
thirty is one ordinary afternoon, and a history that forgot the first half of it
would be a history nobody consults.
"""

DEFAULT_MAX_QUEUED = 1000
"""How many requests may wait at once, when nobody has said.

A ceiling rather than a backlog: one click on a filtered report asks for every
match it has, and what waits is a URL and a little state per request rather
than bytes. It began at five hundred, which was chosen before there was a real
library to watch it against — a directory of a crawled site comes to more than
that often enough, and the cost of the higher number is a few hundred kilobytes
of this process's memory.

The application sets it from :attr:`~maxicrawler.config.Settings.max_queued`,
and the two defaults are held together by a test rather than by hope.
"""

REMOVED = "removed from the queue"
"""Said about a request taken out before it ever started.

Not a failure, and not phrased as one: the person reading it is the person who
clicked the button.
"""


def queue_full(waiting: int, limit: int) -> str:
    """Return what to say to whoever asked for one download too many."""
    return (
        f"the queue is full: {waiting} waiting, and this interface holds {limit}. "
        "Let some of them finish, or cancel what you no longer want."
    )


class Move(StrEnum):
    """Where a waiting request is being moved to."""

    TOP = "top"
    UP = "up"
    DOWN = "down"


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
    is_queued: bool = False
    """Whether this is still waiting rather than being worked on.

    Kept beside the status rather than folded into it. A queued request and one
    the worker has just picked up are both :attr:`DownloadStatus.PENDING` —
    nothing has been transferred either way — but only one of them is somebody's
    turn to wait for, and "waiting" and "starting" are the words for that.
    """

    was_started: bool = False
    """Whether a worker ever picked this up.

    Not the same as "is finished": a request removed from the queue is over and
    was never begun. Without this, its zero elapsed time reads as a transfer
    that took no time rather than one that never happened.
    """

    summary: DownloadSummary | None = None
    """Present once the download is over, whatever way it ended."""

    error: str | None = None
    """Why the download never produced a summary, when it did not."""

    @property
    def is_finished(self) -> bool:
        """Return whether this download has reached a terminal state."""
        return self.summary is not None or self.error is not None

    @property
    def is_running(self) -> bool:
        """Return whether a worker is on this right now."""
        return not self.is_finished and not self.is_queued

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
    """One request, and the handle a page holds on it while it waits and runs."""

    def __init__(self, download_id: str, url: str) -> None:
        self._id = download_id
        self._url = url
        self.control = DownloadControl()
        self._lock = Lock()
        self._queued_at = datetime.now(UTC)
        self._started: float | None = None
        self._started_at: datetime | None = None
        self._finished: float | None = None
        self._is_queued = True
        self._progress = DownloadProgress(label=url, status=DownloadStatus.PENDING, files_total=0)
        self._summary: DownloadSummary | None = None
        self._error: str | None = None
        self._listeners: list[Callable[[DownloadSnapshot], None]] = []

    @property
    def id(self) -> str:
        """Return the identifier this download is addressed by.

        Minted when the request is *queued*, not when it starts. A person can
        look at, cancel and reorder something that has not begun, so it needs a
        name from the moment it exists — and a later duplicate verdict needs
        somewhere to point that is not the URL.
        """
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
        """Return what is true about this download right now.

        The clock starts when the worker picks the request up, not when it was
        queued. An hour spent waiting behind twenty other files is not a slow
        transfer, and a rate computed over it would say so.
        """
        with self._lock:
            if self._started is None:
                elapsed = 0.0
            else:
                finished = self._finished if self._finished is not None else monotonic()
                elapsed = finished - self._started
            return DownloadSnapshot(
                download_id=self._id,
                url=self._url,
                progress=self._progress,
                started_at=self._started_at if self._started_at is not None else self._queued_at,
                elapsed_seconds=elapsed,
                is_queued=self._is_queued,
                was_started=self._started is not None,
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

    def begin(self) -> None:
        """Note that a worker has taken this request off the queue."""
        with self._lock:
            self._is_queued = False
            self._started = monotonic()
            self._started_at = datetime.now(UTC)
        self._announce()

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
            self._is_queued = False
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
            self._is_queued = False
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


@dataclass(frozen=True, slots=True)
class Accepted:
    """What became of a batch of links somebody asked to queue.

    Three outcomes rather than one, because they need three different sentences.
    A malformed link is something to fix; a full queue is something to wait for;
    and neither is a reason to have refused the ones that were fine.
    """

    runs: tuple[DownloadRun, ...]
    rejected: int = 0
    """How many were not absolute HTTP(S) URLs."""

    no_room: int = 0
    """How many were left unqueued because the queue filled up."""

    @property
    def queued(self) -> int:
        """Return how many are now in the queue."""
        return len(self.runs)

    @property
    def is_whole(self) -> bool:
        """Return whether everything asked for was queued."""
        return self.rejected == 0 and self.no_room == 0


@dataclass(frozen=True, slots=True)
class Departed:
    """What the queue has forgotten the details of, kept as numbers.

    A finished run is evicted once there are more of them than the queue keeps,
    and every count a page is built from is a sum over the runs it still holds.
    Without this, a queue draining two hundred files would quietly reset "fifty
    stored" to "one stored" at the fifty-first — a counter that resets itself
    when nobody asked is worse than no counter at all.

    So what is dropped is added here first. The row is gone and the number is
    not, which is the honest shape: the history is a list of what can still be
    looked at, and the readout above it is a total.
    """

    count: int = 0
    """How many runs this stands for, whatever became of them."""

    succeeded: int = 0
    failed: int = 0
    stopped: int = 0
    bytes_written: int = 0
    seconds: float = 0.0
    """How long those runs spent transferring, added up."""

    def including(self, snapshot: DownloadSnapshot) -> "Departed":
        """Return these numbers with one more finished run folded into them."""
        status = snapshot.status
        return Departed(
            count=self.count + 1,
            succeeded=self.succeeded + (1 if status.is_success else 0),
            failed=self.failed + (1 if status is DownloadStatus.FAILED else 0),
            stopped=self.stopped + (1 if status is DownloadStatus.CANCELLED else 0),
            bytes_written=self.bytes_written + snapshot.progress.bytes_written,
            seconds=self.seconds + snapshot.elapsed_seconds,
        )


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    """What the queue holds at the moment it was asked."""

    running: tuple[DownloadSnapshot, ...]
    """The transfers under way, oldest first.

    A tuple rather than one transfer because the queue may drain with more than
    one worker. Everything counted below sums over it, which is the whole of
    what "more than one at a time" means to a page: no number here is ever
    *the* running download's.
    """

    waiting: tuple[DownloadSnapshot, ...]
    finished: tuple[DownloadSnapshot, ...]
    """Newest first, which is the order somebody checking on them reads."""

    is_paused: bool = False
    departed: Departed = Departed()
    """The runs this queue counted and no longer holds."""

    @property
    def active(self) -> DownloadSnapshot | None:
        """Return the oldest transfer under way, or ``None``.

        For the parts of the interface that still speak in the singular. They
        are what the next commit is about; until then this is exactly the
        running download, because exactly one worker takes requests off.
        """
        return self.running[0] if self.running else None

    @property
    def remaining(self) -> int:
        """Return how many downloads have still to happen, the running ones included."""
        return len(self.waiting) + len(self.running)

    @property
    def is_busy(self) -> bool:
        """Return whether there is anything left to do."""
        return self.remaining > 0

    @property
    def succeeded(self) -> int:
        """Return how many finished downloads the library has something from."""
        return self.departed.succeeded + sum(1 for run in self.finished if run.status.is_success)

    @property
    def failed(self) -> int:
        """Return how many finished downloads ended in a failure."""
        return self.departed.failed + sum(
            1 for run in self.finished if run.status is DownloadStatus.FAILED
        )

    @property
    def stopped(self) -> int:
        """Return how many were cancelled, in the queue or during the transfer."""
        return self.departed.stopped + sum(
            1 for run in self.finished if run.status is DownloadStatus.CANCELLED
        )

    @property
    def done(self) -> int:
        """Return how many downloads have reached an end, however they ended.

        Counted rather than added up out of the three verdicts, so a status
        added later cannot make this quietly disagree with the number of things
        that actually happened.
        """
        return self.departed.count + len(self.finished)

    @property
    def known(self) -> int:
        """Return how many downloads this queue is counting in total.

        What "forty-three of two hundred" is measured against. It grows when
        somebody queues more, and that is not a flaw in it: there is no batch
        here, only a queue, and the honest denominator is everything asked for
        so far rather than a total somebody has to have committed to.
        """
        return self.done + self.remaining

    @property
    def bytes_written(self) -> int:
        """Return how many bytes this queue has moved, the running ones included."""
        written = sum(run.progress.bytes_written for run in self.finished)
        under_way = sum(run.progress.bytes_written for run in self.running)
        return self.departed.bytes_written + written + under_way

    @property
    def transfer_seconds(self) -> float:
        """Return how long this queue has spent actually moving bytes.

        The transfers added together, not the wall clock. A queue that sat
        paused overnight did not get slower while it was paused, and a rate
        divided by the hours nobody was downloading would say it did.

        Added together across transfers that overlap, deliberately. Two files
        moving for ten seconds each is twenty seconds of transferring, and the
        rate this divides into is a per-transfer average — which is what the
        page calls it. Wall-clock time would answer a different question, and
        the queue has no clock of its own to answer it with.
        """
        spent = sum(run.elapsed_seconds for run in self.finished)
        under_way = sum(run.elapsed_seconds for run in self.running)
        return self.departed.seconds + spent + under_way


@dataclass(frozen=True, slots=True)
class QueueTally:
    """How much the queue has left, and how much of it went wrong.

    The counts of :class:`QueueSnapshot` without the runs behind them, for the
    pages whose whole interest in the queue is one line at the top. Kept apart
    rather than added as more properties on the snapshot, because what makes it
    worth having is precisely what it does *not* build.
    """

    running: int
    waiting: int
    failed: int
    is_paused: bool = False

    @property
    def remaining(self) -> int:
        """Return how many downloads have still to happen, the running one included."""
        return self.running + self.waiting

    @property
    def is_busy(self) -> bool:
        """Return whether there is anything left to do."""
        return self.remaining > 0

    @property
    def is_worth_saying(self) -> bool:
        """Return whether a page elsewhere in the interface should mention this.

        A paused queue counts even when it is empty. Somebody who paused it an
        hour ago and queues forty links now is owed the reason nothing starts,
        and a silence that is only broken once there is work to do would break
        exactly too late.
        """
        return self.is_busy or self.failed > 0 or self.is_paused


class TransferQueue:
    """The downloads this process knows about, and what is being fetched now.

    Every mutation is guarded by one condition, and a worker waits on that same
    condition rather than polling. What is under way is a mapping rather than a
    field, so nothing here counts on there being exactly one — the second
    worker that fills it is the next thing to arrive, and it should find a queue
    that does not have to be rewritten to admit it.
    """

    def __init__(
        self,
        service: DownloadService,
        *,
        retain: int = DEFAULT_RETAINED_RUNS,
        limit: int = DEFAULT_MAX_QUEUED,
    ) -> None:
        if retain < 1:
            msg = "retain must be at least 1"
            raise ValueError(msg)
        if limit < 1:
            msg = "limit must be at least 1"
            raise ValueError(msg)
        self._service = service
        self._retain = retain
        self._limit = limit
        self._condition = Condition()
        self._runs: OrderedDict[str, DownloadRun] = OrderedDict()
        self._waiting: list[str] = []
        self._targets: dict[str, str] = {}
        # Keyed by run id and ordered by when each transfer started, which is
        # the order a page lists them in. One worker fills it one at a time
        # today; nothing below assumes that.
        self._running: dict[str, DownloadRun] = {}
        self._departed = Departed()
        self._paused = False
        self._closed = False
        self._worker: Thread | None = None

    @property
    def service(self) -> DownloadService:
        """Return the service every download of this queue goes through."""
        return self._service

    @property
    def limit(self) -> int:
        """Return how many requests may wait at once."""
        return self._limit

    def submit(self, url: str) -> DownloadRun:
        """Put *url* at the end of the queue and return its run.

        Raises:
            ValueError: *url* is not an absolute HTTP(S) URL. Checked before
                anything is queued, so a bad link is a message beside the button
                rather than an entry that exists only to have failed.
            QueueFullError: the queue already holds :attr:`limit` requests.
        """
        target = self._service.require_url(url)
        run = DownloadRun(uuid4().hex, strip_fragment(target))
        with self._condition:
            if self._closed:
                msg = "this queue is shutting down"
                raise QueueFullError(msg)
            if len(self._waiting) >= self._limit:
                raise QueueFullError(queue_full(len(self._waiting), self._limit))
            self._runs[run.id] = run
            self._waiting.append(run.id)
            # The URL with its fragment is held here and nowhere else. The run
            # itself never learns the key; see the module docstring.
            self._targets[run.id] = target
            self._evict()
            self._condition.notify_all()
        self._ensure_worker()
        return run

    def room(self) -> int:
        """Return how many more requests would fit right now.

        Asked before resolving a selection rather than after. Working out which
        four hundred URLs somebody meant and then refusing them one at a time
        would be the slowest possible way to say "the queue is full".
        """
        with self._condition:
            return 0 if self._closed else max(0, self._limit - len(self._waiting))

    def submit_all(self, urls: Iterable[str]) -> Accepted:
        """Put every URL of *urls* in the queue, and report what happened.

        Partial by design. A selection of two hundred links where three are
        malformed and the queue has room for a hundred and fifty is not an
        error — it is a job that was mostly done, and the caller is owed the
        numbers rather than an exception that loses the other hundred and
        forty-seven.

        The order they arrive in is the order they are queued in, so a
        selection taken off a sorted report keeps that sorting.
        """
        asked = tuple(urls)
        queued: list[DownloadRun] = []
        rejected = 0
        for index, url in enumerate(asked):
            try:
                queued.append(self.submit(url))
            except ValueError:
                rejected += 1
            except QueueFullError:
                # Full is not "this URL was bad": everything after it would fail
                # the same way, so stop rather than count them all as refusals.
                return Accepted(runs=tuple(queued), rejected=rejected, no_room=len(asked) - index)
        return Accepted(runs=tuple(queued), rejected=rejected)

    def get(self, download_id: str) -> DownloadRun | None:
        """Return the run called *download_id*, if this process still holds it."""
        with self._condition:
            return self._runs.get(download_id)

    def running(self) -> tuple[DownloadRun, ...]:
        """Return the transfers under way right now, oldest first."""
        with self._condition:
            return tuple(self._running.values())

    def active(self) -> DownloadRun | None:
        """Return the oldest transfer under way, if there is one.

        What the parts of the interface that speak of *the* running download
        ask. With one worker taking requests off, it is that download; when
        there are more, those parts are what changes rather than this answer.
        """
        with self._condition:
            return next(iter(self._running.values()), None)

    def pending(self, urls: Iterable[str]) -> frozenset[str]:
        """Return which of *urls* are waiting or being fetched right now.

        Written to the shape of
        :data:`~maxicrawler.app.discovery.StateResolver`, so a report can mark
        its rows without knowing there is a queue behind the answer.

        Waiting and running, and deliberately nothing else. A finished transfer
        has left the queue for the library, and a failed one is not in a line —
        both are states of their own to answer elsewhere, and calling either
        "queued" would put a mark on a row that no longer earns it.

        Fragments are stripped to compare, because a run is stored without one
        (ADR-020), and kept in the answer, because the caller's URL is the one
        that still carries the key.
        """
        asked = tuple(urls)
        if not asked:
            return frozenset()
        with self._condition:
            queued = {self._runs[run_id].url for run_id in self._waiting}
            queued.update(run.url for run in self._running.values())
        return frozenset(url for url in asked if strip_fragment(url) in queued)

    def position_of(self, download_id: str) -> int | None:
        """Return where a waiting request sits, counting from one.

        ``None`` for one that is running, finished, or unknown — all of which
        are "not waiting", and none of which has a place in the line.
        """
        with self._condition:
            if download_id not in self._waiting:
                return None
            return self._waiting.index(download_id) + 1

    def tally(self) -> QueueTally:
        """Return the queue's counts, without a snapshot of every run in it.

        What a page whose interest in the queue is one line needs.
        :meth:`snapshot` builds a :class:`DownloadSnapshot` per waiting request
        as well, and a full queue is five hundred of them — a cost worth paying
        to render the queue's own table and worth not paying on every other page
        in the interface.

        The failures are counted from the runs that are neither waiting nor
        running, which is bounded by how many finished ones the queue retains,
        plus the ones already evicted. Counted through the same snapshot the
        tables read rather than from a second reading of the same fields: a
        tally that could disagree with the page it sits above would be worse
        than no tally.
        """
        with self._condition:
            queued = set(self._waiting)
            waiting = len(self._waiting)
            under_way = len(self._running)
            busy = set(self._running)
            paused = self._paused
            departed = self._departed
            done = [run for key, run in self._runs.items() if key not in queued and key not in busy]
        # Outside the lock: each run guards its own fields, and holding the
        # queue's condition while asking fifty of them is a wait nobody needs.
        failed = sum(1 for run in done if run.snapshot().status is DownloadStatus.FAILED)
        return QueueTally(
            running=under_way,
            waiting=waiting,
            failed=departed.failed + failed,
            is_paused=paused,
        )

    def snapshot(self) -> QueueSnapshot:
        """Return what the queue holds right now.

        Built under the lock so the three lists agree with each other. A page
        that showed a request as both active and waiting would be describing a
        state that never existed.
        """
        with self._condition:
            under_way = tuple(self._running.values())
            busy = set(self._running)
            waiting = tuple(self._runs[run_id] for run_id in self._waiting)
            finished = tuple(
                run
                for run in reversed(self._runs.values())
                if run.id not in busy and run.id not in self._waiting
            )
            paused = self._paused
            departed = self._departed
        return QueueSnapshot(
            running=tuple(run.snapshot() for run in under_way),
            waiting=tuple(run.snapshot() for run in waiting),
            finished=tuple(run.snapshot() for run in finished),
            is_paused=paused,
            departed=departed,
        )

    def pause(self) -> None:
        """Stop taking new requests off the queue.

        The transfer already running is left to finish. Stopping it as well
        would be a second decision wearing one button: "let me think" and "undo
        what is happening" are different intentions, and the running download
        has its own Stop.
        """
        with self._condition:
            self._paused = True
            self._condition.notify_all()

    def resume(self) -> None:
        """Start taking requests off the queue again."""
        with self._condition:
            self._paused = False
            self._condition.notify_all()

    @property
    def is_paused(self) -> bool:
        """Return whether the worker is holding off."""
        with self._condition:
            return self._paused

    def cancel(self, download_id: str) -> bool:
        """Take one request out of the queue, or stop it if it is running.

        Returns whether anything was done. A request that already finished is
        not an error to cancel — it is a second click on a button that was true
        a moment ago, and answering ``False`` says so without an error page.
        """
        with self._condition:
            run = self._runs.get(download_id)
            if run is None:
                return False
            if download_id in self._waiting:
                self._waiting.remove(download_id)
                # The target stays. It is what a retry needs, and dropping it
                # here would make "remove, then think better of it" the one
                # thing this queue cannot undo. It goes when the run does.
                self._condition.notify_all()
            elif download_id in self._running:
                run.stop()
                return True
            else:
                return False
        # Outside the lock: `complete` announces to listeners, and calling into
        # somebody else's code while holding the queue's condition is how one
        # slow reader stops every transfer in the process.
        run.complete(
            DownloadSummary(
                url=run.url, status=DownloadStatus.CANCELLED, label=run.url, reason=REMOVED
            )
        )
        return True

    def retry(self, download_id: str) -> DownloadRun | None:
        """Queue the same URL again, and return the new request.

        A new run with a new identity rather than a reset of the old one. What
        happened the first time is a fact, and a history that overwrote its own
        failures would be one nobody could read. ``None`` when the request is
        unknown or has not finished — there is nothing to retry about a download
        that is still going.

        Idempotent by construction: a resource the library already holds is
        skipped by the worker below, so retrying something that in fact
        succeeded costs one skipped outcome and no bytes.

        Raises:
            QueueFullError: the queue has no room for it.
        """
        with self._condition:
            run = self._runs.get(download_id)
            target = self._targets.get(download_id)
            if run is None or target is None or not run.snapshot().is_finished:
                return None
        return self.submit(target)

    def retry_all(self) -> Accepted:
        """Queue every finished request that did not arrive, oldest first.

        What the history already offers row by row, in one click. Which is why
        it takes the same set those rows do — everything that ended without the
        file being stored, a removed request included. Somebody who stopped
        twenty downloads and has thought better of it is asking exactly this,
        and a button that quietly skipped them would be a different button from
        the twenty beside it.

        Oldest first, so what comes back keeps the order it had. Bounded by what
        this process still holds: a run evicted an hour ago has no URL left to
        retry, which is the same reason its row is not on the page either.
        """
        with self._condition:
            targets = [
                target
                for key, run in self._runs.items()
                if key not in self._waiting
                and key not in self._running
                and (target := self._targets.get(key)) is not None
                and _is_unarrived(run)
            ]
        # Outside the lock: `submit` takes the same condition, and asking for it
        # while holding it is the one way this deadlocks.
        return self.submit_all(targets)

    def forget_finished(self) -> int:
        """Drop every finished run, and return how many rows that was.

        Nothing waiting or running is touched. This is about a list that has got
        long enough to stop being readable, not about the work — and the files
        are in the library either way, which is what the footnote under the table
        says.

        The counters go with the rows, deliberately. They are totals *over* this
        list, and a readout saying "43 stored" above an empty history would be
        describing something nobody can look at. Clearing is the one way they
        reset, which is what makes them trustworthy the rest of the time.
        """
        with self._condition:
            keys = [
                key
                for key, run in self._runs.items()
                if key not in self._waiting
                and key not in self._running
                and run.snapshot().is_finished
            ]
            for key in keys:
                del self._runs[key]
                self._targets.pop(key, None)
            self._departed = Departed()
        return len(keys)

    def move(self, download_id: str, where: Move) -> bool:
        """Move a waiting request within the queue, and say whether it moved.

        Only the waiting ones. What is being transferred right now is not a
        position in a line, and the request at the front has nowhere to go.
        """
        with self._condition:
            if download_id not in self._waiting:
                return False
            index = self._waiting.index(download_id)
            target = _moved_to(index, where, len(self._waiting))
            if target == index:
                return False
            self._waiting.pop(index)
            self._waiting.insert(target, download_id)
            return True

    def recent(self, limit: int = DEFAULT_RETAINED_RUNS) -> tuple[DownloadRun, ...]:
        """Return the most recently submitted runs, newest first."""
        with self._condition:
            newest_first = tuple(reversed(self._runs.values()))
        return newest_first[:limit]

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop accepting downloads, asking a running transfer to stop first.

        What is still waiting is dropped rather than transferred: a queue that
        lives in memory ends with the process, and a shutdown that ran twenty
        more files first would be a shutdown nobody could rely on.
        """
        with self._condition:
            self._closed = True
            self._waiting.clear()
            self._targets.clear()
            under_way = tuple(self._running.values())
            worker = self._worker
            self._condition.notify_all()
        for run in under_way:
            run.stop()
        if wait and worker is not None:
            worker.join(timeout=SHUTDOWN_TIMEOUT)

    def _ensure_worker(self) -> None:
        """Start the draining thread, once, the first time there is work.

        Lazily rather than in ``__init__`` so an application that never
        downloads anything never starts a thread — which is most of the tests
        and every session that only crawls.
        """
        with self._condition:
            if self._worker is not None or self._closed:
                return
            self._worker = Thread(target=self._drain, name="maxidownload", daemon=True)
            worker = self._worker
        worker.start()

    def _drain(self) -> None:
        """Take one request at a time until the queue is closed."""
        while True:
            with self._condition:
                while not self._closed and (self._paused or not self._waiting):
                    self._condition.wait()
                if self._closed:
                    return
                run_id = self._waiting.pop(0)
                run = self._runs[run_id]
                target = self._targets[run_id]
                self._running[run_id] = run
            try:
                run.begin()
                self._execute(run, target)
            finally:
                with self._condition:
                    self._running.pop(run_id, None)
                    self._condition.notify_all()

    def _execute(self, run: DownloadRun, url: str) -> None:
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

    def _evict(self) -> None:
        """Drop the oldest finished runs once there are more than we keep.

        Callers hold the condition. A waiting or running download is never
        evicted, because the queue is how a request finds it again — and its
        target goes with it, which is what bounds how long a credential is held.

        What each dropped run counted for is kept, in :class:`Departed`. This is
        the one place a run leaves without being asked to, so it is the one
        place that has to say so before the numbers go with it.
        """
        finished = [
            (key, snapshot)
            for key, run in self._runs.items()
            if key not in self._waiting and (snapshot := run.snapshot()).is_finished
        ]
        for key, snapshot in finished[: max(0, len(finished) - self._retain)]:
            self._departed = self._departed.including(snapshot)
            del self._runs[key]
            self._targets.pop(key, None)


SHUTDOWN_TIMEOUT = 30.0
"""How long a shutdown waits for the worker to leave its current transfer.

A bound rather than a promise. The transfer is asked to stop and does so within
a chunk; this is what keeps a provider that has stopped answering from holding
the whole process open.
"""


def _is_unarrived(run: DownloadRun) -> bool:
    """Return whether *run* is over and asking again could end differently.

    The same question the history's own "Try again" asks of one row: a dead
    share, a broken transfer, and a stop somebody has since reconsidered are one
    set, because in all three the file is not there *and* a second attempt could
    change that. A payload a configured limit turned away is in neither half —
    see :attr:`~maxicrawler.domain.downloads.DownloadStatus.invites_retry`,
    which is where the three places that ask this agree on the answer.
    """
    snapshot = run.snapshot()
    return snapshot.is_finished and snapshot.status.invites_retry


def _moved_to(index: int, where: Move, total: int) -> int:
    """Return where a request at *index* ends up after *where*."""
    match where:
        case Move.TOP:
            return 0
        case Move.UP:
            return max(0, index - 1)
        case _:
            return min(total - 1, index + 1)
