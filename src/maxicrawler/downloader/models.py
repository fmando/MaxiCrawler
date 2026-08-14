"""What the download layer plans, executes, and reports.

These models carry a :class:`~pathlib.Path` and therefore live outside
:mod:`maxicrawler.domain`, for the same reason
:class:`~maxicrawler.documents.models.Document` does. They stay free of any
provider knowledge: a job is a reference and a few things already known about
it, whoever produced them.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from maxicrawler.domain import Checksum, DownloadStatus, ResourceRef

ResourceIdentity = tuple[str, str, str]
"""Provider, container, and resource — what makes two jobs the same job."""


@dataclass(frozen=True, slots=True)
class DownloadJob:
    """One resource a worker has been asked to transfer.

    ``name`` and ``size`` are what was already known when the job was planned,
    which for an entry of a shared folder is everything and for a bare file
    link is nothing. Neither is required: a provider states both again as it
    opens the transfer, and this is only what lets progress start out
    informative.
    """

    ref: ResourceRef
    origin: str | None = None
    """The document the URL was found in, when it came from one."""

    name: str | None = None
    size: int | None = None
    priority: int = 0
    discovered_at: datetime | None = None

    @property
    def identity(self) -> ResourceIdentity:
        """Return what makes this job the same as another.

        Derived from the reference alone, never from the credential, so the
        same resource reached through two links queues once.
        """
        return (self.ref.provider, self.ref.parent_id or "", self.ref.resource_id)

    @property
    def source_url(self) -> str:
        """Return the share URL, which never carries a credential."""
        return self.ref.url

    @property
    def label(self) -> str:
        """Return the name to show a human, falling back to the identifier."""
        return self.name or self.ref.resource_id


@dataclass(frozen=True, slots=True)
class DownloadOutcome:
    """What became of one job."""

    job: DownloadJob
    status: DownloadStatus
    path: Path | None = None
    bytes_written: int = 0
    checksums: tuple[Checksum, ...] = ()
    reason: str | None = None
    """Why a job was skipped, refused or failed; ``None`` for a plain success."""

    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def label(self) -> str:
        """Return the name to show a human."""
        return self.job.label

    @property
    def succeeded(self) -> bool:
        """Return whether the library holds the resource afterwards."""
        return self.status.is_success


@dataclass(frozen=True, slots=True)
class UnresolvedSource:
    """A URL that never became a job, and why.

    This is not an error state. A revoked share, a folder holding no files, and
    a host nobody supports are all ordinary findings of a run, and reporting
    them beside the outcomes is what makes a run's account complete.
    """

    url: str
    reason: str


@dataclass(frozen=True, slots=True)
class DownloadPlan:
    """Everything a run intends to do, decided before anything is transferred.

    Planning is separate from running so that ``--dry-run`` is the same code
    path as a real run minus its last step, rather than a second
    implementation that can drift.
    """

    jobs: tuple[DownloadJob, ...] = ()
    unresolved: tuple[UnresolvedSource, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Return whether there is nothing to transfer."""
        return not self.jobs

    @property
    def total_size(self) -> int | None:
        """Return the planned byte count, or ``None`` if any part is unknown."""
        if not self.jobs:
            return 0
        if any(job.size is None for job in self.jobs):
            return None
        return sum(job.size or 0 for job in self.jobs)


@dataclass(frozen=True, slots=True)
class DownloadReport:
    """The account of one finished run."""

    plan: DownloadPlan
    outcomes: tuple[DownloadOutcome, ...] = ()
    library_root: Path | None = None

    @property
    def unresolved(self) -> tuple[UnresolvedSource, ...]:
        """Return the URLs that never became jobs."""
        return self.plan.unresolved

    @property
    def completed(self) -> tuple[DownloadOutcome, ...]:
        """Return the jobs whose content was transferred."""
        return self._with(DownloadStatus.COMPLETED)

    @property
    def skipped(self) -> tuple[DownloadOutcome, ...]:
        """Return the jobs the library already held."""
        return self._with(DownloadStatus.SKIPPED)

    @property
    def refused(self) -> tuple[DownloadOutcome, ...]:
        """Return the jobs a configured rule turned away.

        Kept out of :attr:`succeeded` deliberately — that is, a refusal does not
        make a run unsuccessful. A cancellation does, because somebody wanted
        those bytes and stopped getting them; a refusal is the opposite, a limit
        this installation set doing precisely what it was set to do. A run over
        two hundred links that leaves forty thumbnails behind did what it was
        told, and an exit code saying otherwise would teach whoever reads it to
        stop reading it.
        """
        return self._with(DownloadStatus.REFUSED)

    @property
    def failed(self) -> tuple[DownloadOutcome, ...]:
        """Return the jobs that did not finish."""
        return self._with(DownloadStatus.FAILED)

    @property
    def cancelled(self) -> tuple[DownloadOutcome, ...]:
        """Return the jobs somebody asked to stop.

        Kept apart from :attr:`failed` on purpose: a run that was stopped is
        not a run that broke, and a caller deciding what to tell a person needs
        to be able to tell those two apart.
        """
        return self._with(DownloadStatus.CANCELLED)

    @property
    def bytes_written(self) -> int:
        """Return how many bytes this run actually stored."""
        return sum(outcome.bytes_written for outcome in self.outcomes)

    @property
    def succeeded(self) -> bool:
        """Return whether the run left nothing unaccounted for.

        A skipped download counts as a success; an unresolved source does not,
        because the user asked for something that did not happen. Neither does
        a cancelled one — nothing went wrong, but the resource is not here.
        """
        return not self.failed and not self.cancelled and not self.unresolved

    def _with(self, status: DownloadStatus) -> tuple[DownloadOutcome, ...]:
        """Return the outcomes in *status*, in the order they were produced."""
        return tuple(outcome for outcome in self.outcomes if outcome.status is status)
