"""The provider-independent download manager.

The manager answers *"how are downloads executed?"* and nothing else. It knows
that a source becomes URLs, that URLs become jobs, that jobs wait in a queue,
that a worker hands a resource to whichever provider claims it, and that what
comes back belongs in the library. It does not know what a Mega link is, how a
share is decrypted, or what a transfer URL looks like — and adding Pixeldrain,
GoFile, or MediaFire requires no change to any line of this module.

The single rule that keeps it that way: nothing here branches on a provider
name. Where behaviour differs, it is asked for through the provider protocol or
declared through :class:`~maxicrawler.domain.providers.ProviderCapability`.
"""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from maxicrawler.domain import Checksum, DownloadStatus
from maxicrawler.downloader.control import DownloadControl
from maxicrawler.downloader.errors import DownloadCancelledError, DownloadError
from maxicrawler.downloader.models import (
    DownloadJob,
    DownloadOutcome,
    DownloadPlan,
    DownloadReport,
)
from maxicrawler.downloader.planner import DownloadPlanner
from maxicrawler.downloader.progress import NullProgressReporter, ProgressReporter
from maxicrawler.downloader.queue import DownloadQueue
from maxicrawler.downloader.sink import DEFAULT_HASH_ALGORITHM, LibrarySink
from maxicrawler.downloader.sources import SourceResolver
from maxicrawler.library import (
    ContentRecord,
    Library,
    LibraryEntry,
    LibraryError,
    ResourceRecord,
    new_record,
)
from maxicrawler.providers import ProviderError, ProviderRegistry

Clock = Callable[[], datetime]
"""Injected so timings can be asserted without freezing the real clock."""

STOPPED = "stopped on request"
"""Said about a transfer nobody wanted finished. Not a failure, and not phrased
as one: the person reading it is the person who asked."""


class DownloadWorker:
    """Executes one job at a time, from the queue to the library.

    The worker holds no state between jobs, which is what makes running
    several of them a matter of calling :meth:`execute` from more than one
    thread rather than a redesign.

    Every job ends in an outcome. A provider that raises, a store that cannot
    be written, a payload that arrives short — all of them become a failed
    outcome with a reason, because a run over two hundred links must report on
    all two hundred rather than stop at the first bad one.
    """

    def __init__(
        self,
        providers: ProviderRegistry,
        library: Library,
        *,
        reporter: ProgressReporter | None = None,
        clock: Clock | None = None,
        algorithm: str = DEFAULT_HASH_ALGORITHM,
        control: DownloadControl | None = None,
    ) -> None:
        self._providers = providers
        self._library = library
        self._reporter = reporter if reporter is not None else NullProgressReporter()
        self._clock = clock if clock is not None else _utc_now
        self._algorithm = algorithm
        self._control = control

    def execute(self, job: DownloadJob) -> DownloadOutcome:
        """Transfer *job* into the library and report what happened."""
        started = self._clock()
        if self._control is not None and self._control.stop_requested:
            # Asked to stop before this job began. Not started rather than
            # abandoned halfway, but the same answer to whoever asked.
            return self._finish(job, DownloadStatus.CANCELLED, started, reason=STOPPED)
        entry = self._library.entry(job.ref)
        try:
            record = entry.read()
        except LibraryError as error:
            return self._finish(job, DownloadStatus.FAILED, started, reason=str(error))
        stored = _stored_payload(entry, record)
        if stored is not None:
            return self._finish(
                job,
                DownloadStatus.SKIPPED,
                started,
                reason="the library already holds it",
                path=stored,
            )
        return self._transfer(job, entry, record, started)

    def _transfer(
        self,
        job: DownloadJob,
        entry: LibraryEntry,
        previous: ResourceRecord | None,
        started: datetime,
    ) -> DownloadOutcome:
        """Fetch *job* into *entry*, recording the result either way."""
        attempts = (previous.attempts if previous is not None else 0) + 1
        self._reporter.started(job, job.size)
        try:
            provider = self._providers.get(job.ref.provider)
            with LibrarySink(
                entry,
                algorithm=self._algorithm,
                on_progress=lambda written: self._reporter.advanced(job, written),
                control=self._control,
            ) as sink:
                descriptor = provider.download(job.ref, sink)
                content = sink.commit()
        except DownloadCancelledError:
            # Nothing is recorded. A cancelled transfer leaves the library
            # exactly as it was, and a stored record saying "failed" would turn
            # somebody's own decision into a fault they later have to explain —
            # and would make the next run count an attempt nobody made.
            return self._finish(job, DownloadStatus.CANCELLED, started, reason=STOPPED)
        except (ProviderError, LibraryError, DownloadError, OSError) as error:
            reason = str(error)
            self._store(
                entry,
                self._record(job, entry, DownloadStatus.FAILED, attempts, previous, error=reason),
            )
            return self._finish(job, DownloadStatus.FAILED, started, reason=reason)
        self._store(
            entry,
            self._record(
                job,
                entry,
                DownloadStatus.COMPLETED,
                attempts,
                previous,
                name=descriptor.name or job.name,
                content=content,
            ),
        )
        return self._finish(
            job,
            DownloadStatus.COMPLETED,
            started,
            path=entry.path / content.path,
            bytes_written=content.size,
            checksums=content.checksums,
        )

    def _record(
        self,
        job: DownloadJob,
        entry: LibraryEntry,
        status: DownloadStatus,
        attempts: int,
        previous: ResourceRecord | None,
        *,
        name: str | None = None,
        content: ContentRecord | None = None,
        error: str | None = None,
    ) -> ResourceRecord:
        """Return the metadata document describing *job* in *status*.

        Built fresh from the job rather than edited into the old document, so
        what a completed transfer says is decided by the transfer and not by
        whatever the entry happened to hold before it.

        Two members are the exception, and they are the ones this layer has no
        opinion about. **A judgement and an unrecognised member are carried
        forward from *previous* verbatim.** Rebuilding them away would mean
        somebody's decision to keep a file was erased by fetching it a second
        time — and would quietly break the promise ADR-013 makes about unknown
        members surviving, which `to_document` keeps and this method used not to.
        """
        record = new_record(
            job.ref,
            entry.key,
            status=status,
            name=name if name is not None else job.name,
            source_document=job.origin,
            discovered_at=job.discovered_at,
        )
        return replace(
            record,
            downloaded_at=self._clock() if status is DownloadStatus.COMPLETED else None,
            attempts=attempts,
            error=error,
            content=content,
            review=None if previous is None else previous.review,
            extra={} if previous is None else previous.extra,
        )

    @staticmethod
    def _store(entry: LibraryEntry, record: ResourceRecord) -> None:
        """Write *record*, tolerating a store that refuses.

        A failure to record a failure must not replace the reason the download
        failed in the first place, which is what the outcome already carries.
        """
        with suppress(LibraryError, OSError):
            entry.write(record)

    def _finish(
        self,
        job: DownloadJob,
        status: DownloadStatus,
        started: datetime,
        *,
        path: Path | None = None,
        bytes_written: int = 0,
        checksums: tuple[Checksum, ...] = (),
        reason: str | None = None,
    ) -> DownloadOutcome:
        """Return the outcome for *job* and report it to the progress reporter."""
        outcome = DownloadOutcome(
            job=job,
            status=status,
            path=path,
            bytes_written=bytes_written,
            checksums=checksums,
            reason=reason,
            started_at=started,
            finished_at=self._clock(),
        )
        self._reporter.finished(job, outcome)
        return outcome


class DownloadManager:
    """Executes downloads for any provider, into the library.

    The manager is the composition point of the layer and owns the sequence:

    ``source → URLs → plan → queue → worker → library``

    Each arrow is a collaborator that can be replaced, which is what makes the
    whole run testable without a network, a disk, or a terminal.
    """

    def __init__(
        self,
        providers: ProviderRegistry,
        library: Library,
        *,
        sources: SourceResolver | None = None,
        planner: DownloadPlanner | None = None,
        worker: DownloadWorker | None = None,
        reporter: ProgressReporter | None = None,
        clock: Clock | None = None,
        control: DownloadControl | None = None,
    ) -> None:
        self._providers = providers
        self._library = library
        self._reporter = reporter if reporter is not None else NullProgressReporter()
        self._sources = sources if sources is not None else SourceResolver()
        self._planner = (
            planner
            if planner is not None
            else DownloadPlanner(providers, clock=clock if clock is not None else _utc_now)
        )
        self._control = control
        self._worker = (
            worker
            if worker is not None
            else DownloadWorker(
                providers, library, reporter=self._reporter, clock=clock, control=control
            )
        )

    @property
    def library(self) -> Library:
        """Return the library this manager stores into."""
        return self._library

    def plan(self, source: str, *, inspect_files: bool = False) -> DownloadPlan:
        """Return what downloading *source* would transfer, without doing it.

        *inspect_files* is handed to the planner unchanged: it asks the provider
        to describe plain file links too, so the plan states their names and
        sizes at the cost of one request each.

        Raises:
            SourceError: *source* is neither an HTTP(S) URL nor a readable path.
        """
        return self._planner.plan(self._sources.resolve(source), inspect_files=inspect_files)

    def run(self, plan: DownloadPlan) -> DownloadReport:
        """Execute *plan* and return its account.

        The queue is drained by a single worker. Replacing this loop with a
        thread pool is the whole of what parallel downloading needs: the queue
        is already guarded and the worker already holds no state between jobs.

        A stop is honoured between jobs *and* inside one — the worker checks
        before it starts, the sink checks on every chunk. A link that turned
        out to be a folder of two hundred files therefore stops at the file it
        is on rather than after all of them.
        """
        self._library.initialize()
        queue = DownloadQueue(plan.jobs)
        outcomes: list[DownloadOutcome] = []
        self._reporter.begin()
        try:
            for job in queue.drain():
                outcomes.append(self._worker.execute(job))
        finally:
            self._reporter.end()
        return DownloadReport(plan=plan, outcomes=tuple(outcomes), library_root=self._library.root)

    def download(self, source: str, *, inspect_files: bool = False) -> DownloadReport:
        """Plan and execute a download of *source*."""
        return self.run(self.plan(source, inspect_files=inspect_files))


def _stored_payload(entry: LibraryEntry, record: ResourceRecord | None) -> Path | None:
    """Return the stored payload path when *entry* already holds the resource.

    Both the record and the file are checked. A record claiming completion
    whose payload has been deleted is not a finished download, which is what
    makes a damaged library repairable by simply running the download again.
    """
    if record is None or not record.is_complete or record.content is None:
        return None
    path = entry.path / record.content.path
    return path if path.is_file() else None


def _utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)
