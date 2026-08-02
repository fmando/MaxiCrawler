"""How a run reports what it is doing.

Progress is a protocol rather than a print statement, so the manager stays
usable from a script, a future GUI, and a future API without any of them
inheriting a terminal. The Rich implementation is one adapter among several,
and the null one is the default: a library caller that asks for nothing gets
nothing.

Rich renders to standard error. Standard output then carries only the final
report, which keeps ``maxicrawler download … > report.txt`` meaningful and
keeps a progress bar out of anything a script parses.
"""

from types import TracebackType
from typing import Protocol, runtime_checkable

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from maxicrawler.downloader.models import DownloadJob, DownloadOutcome, ResourceIdentity


@runtime_checkable
class ProgressReporter(Protocol):
    """Observes a download run as it happens.

    The run brackets itself with :meth:`begin` and :meth:`end`, and reports
    each transfer with :meth:`started`, any number of :meth:`advanced` calls,
    and one :meth:`finished`. A job the library already held is only
    :meth:`finished`: nothing was transferred, so there was nothing to show.
    """

    def begin(self) -> None:
        """Announce that the run is starting."""
        ...

    def end(self) -> None:
        """Announce that the run has ended, however it ended."""
        ...

    def started(self, job: DownloadJob, size: int | None) -> None:
        """Announce a transfer of *size* bytes, which may be unknown."""
        ...

    def advanced(self, job: DownloadJob, written: int) -> None:
        """Report that *written* bytes have arrived in total, not since last."""
        ...

    def finished(self, job: DownloadJob, outcome: DownloadOutcome) -> None:
        """Report the verdict for *job*."""
        ...


class NullProgressReporter:
    """Reports nothing. The default, so a library caller stays silent."""

    def begin(self) -> None:
        """Do nothing."""

    def end(self) -> None:
        """Do nothing."""

    def started(self, job: DownloadJob, size: int | None) -> None:
        """Do nothing."""

    def advanced(self, job: DownloadJob, written: int) -> None:
        """Do nothing."""

    def finished(self, job: DownloadJob, outcome: DownloadOutcome) -> None:
        """Do nothing."""


class RichProgressReporter:
    """Renders one Rich progress bar per transfer.

    A transfer whose size the provider did not state gets an indeterminate bar
    rather than a bar stuck at zero, and is given its final total once the
    payload has arrived, so a finished run shows every bar complete.
    """

    def __init__(self, console: Console | None = None, *, transient: bool = False) -> None:
        self._console = console if console is not None else Console(stderr=True)
        self._progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=self._console,
            transient=transient,
        )
        self._tasks: dict[ResourceIdentity, TaskID] = {}

    def begin(self) -> None:
        """Start rendering."""
        self._progress.start()

    def end(self) -> None:
        """Stop rendering, leaving the finished bars in place."""
        self._progress.stop()

    def started(self, job: DownloadJob, size: int | None) -> None:
        """Add a bar for *job*."""
        self._tasks[job.identity] = self._progress.add_task(job.label, total=size)

    def advanced(self, job: DownloadJob, written: int) -> None:
        """Move the bar of *job* to *written* bytes."""
        task = self._tasks.get(job.identity)
        if task is not None:
            self._progress.update(task, completed=written)

    def finished(self, job: DownloadJob, outcome: DownloadOutcome) -> None:
        """Complete the bar of *job*, naming what happened when it went wrong."""
        task = self._tasks.pop(job.identity, None)
        if task is None:
            return
        description = job.label if outcome.succeeded else f"{job.label} — {outcome.status.value}"
        self._progress.update(
            task,
            description=description,
            completed=outcome.bytes_written,
            total=outcome.bytes_written,
        )

    def __enter__(self) -> "RichProgressReporter":
        """Start rendering and return the reporter."""
        self.begin()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop rendering."""
        self.end()
