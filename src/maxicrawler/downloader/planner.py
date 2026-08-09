"""Deciding what a run will transfer, before it transfers anything.

Planning is a separate step from running for two reasons. It makes ``--dry-run``
the same code path minus its last stage rather than a second implementation
that can drift, and it means every decision that could go wrong — an
unclassifiable URL, a provider that cannot download, a revoked share — is made
and reported before a single byte moves.

The planner is where a container becomes many jobs. A folder share is
enumerated through the provider's own :meth:`inspect`, and each file it holds
becomes its own job, so the rest of the layer only ever sees single resources.

A **file** link is planned without asking anybody by default, because a run over
two hundred links must not become two hundred extra requests to learn what the
transfer is about to state anyway. ``inspect_files`` turns that trade the other
way for a caller downloading one deliberate link, which then knows the name and
the size before the first byte moves — enough to draw a progress bar with a
denominator and to name the file rather than its handle.
"""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from maxicrawler.domain import (
    Availability,
    ProviderCapability,
    ResourceInspection,
    ResourceKind,
    ResourceRef,
    UrlRecord,
)
from maxicrawler.downloader.models import DownloadJob, DownloadPlan, UnresolvedSource
from maxicrawler.downloader.sources import SourceItem
from maxicrawler.plugins import PluginResolver, create_default_registry
from maxicrawler.providers import ProviderError, ProviderRegistry, ResourceProvider
from maxicrawler.utils import normalize_url, strip_fragment

Clock = Callable[[], datetime]
"""Injected so a planned timestamp can be asserted without freezing time."""


class DownloadPlanner:
    """Turns source URLs into the jobs a worker can execute.

    The planner performs network access — enumerating a container needs the
    provider to answer — but transfers nothing. Every failure it meets becomes
    an :class:`~maxicrawler.downloader.models.UnresolvedSource` rather than an
    exception, because one dead link in a list of two hundred must not stop the
    other one hundred and ninety-nine.
    """

    def __init__(
        self,
        providers: ProviderRegistry,
        *,
        resolver: PluginResolver | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._providers = providers
        self._resolver = (
            resolver if resolver is not None else PluginResolver(create_default_registry())
        )
        self._clock = clock if clock is not None else _utc_now

    def plan(self, items: Iterable[SourceItem], *, inspect_files: bool = False) -> DownloadPlan:
        """Return what a run over *items* would transfer.

        *inspect_files* asks the provider to describe a plain file link as well,
        so the resulting job carries its name and size. It costs one request per
        link and is off by default; see the module docstring for which caller
        wants which.
        """
        jobs: list[DownloadJob] = []
        unresolved: list[UnresolvedSource] = []
        for item in items:
            planned, skipped = self._plan_one(item, inspect_files=inspect_files)
            jobs.extend(planned)
            unresolved.extend(skipped)
        return DownloadPlan(jobs=tuple(jobs), unresolved=tuple(unresolved))

    def _plan_one(
        self, item: SourceItem, *, inspect_files: bool
    ) -> tuple[tuple[DownloadJob, ...], tuple[UnresolvedSource, ...]]:
        """Return the jobs and findings one source URL produces."""
        safe_url = strip_fragment(item.url)
        try:
            record = UrlRecord(raw_url=item.url, normalized_url=normalize_url(item.url))
        except ValueError:
            return (), (UnresolvedSource(safe_url, "not an absolute HTTP(S) URL"),)
        classification = self._resolver.resolve(record).classification
        if classification is None:
            return (), (UnresolvedSource(safe_url, "no plugin can classify this link"),)
        provider = self._providers.resolve(classification)
        if provider is None:
            return (), (UnresolvedSource(safe_url, "no provider can handle this link"),)
        if not provider.metadata.supports(ProviderCapability.DOWNLOAD):
            reason = f"the {provider.metadata.label} provider cannot transfer content"
            return (), (UnresolvedSource(safe_url, reason),)
        try:
            return self._expand(
                provider,
                provider.reference(classification),
                item,
                inspect_files=inspect_files,
            )
        except ProviderError as error:
            return (), (UnresolvedSource(safe_url, str(error)),)

    def _expand(
        self,
        provider: ResourceProvider,
        ref: ResourceRef,
        item: SourceItem,
        *,
        inspect_files: bool,
    ) -> tuple[tuple[DownloadJob, ...], tuple[UnresolvedSource, ...]]:
        """Return the jobs *ref* stands for, enumerating a container first.

        A file link needs no request unless one was asked for: everything the
        job needs is either in the link or will be stated as the transfer opens.
        Anything else is inspected, because only the provider can say whether it
        is one resource or many.

        Asking about a file link takes the same path a container does, which is
        why *inspect_files* is one condition rather than a branch of its own: an
        inspection that finds the resource gone reports it as unresolved before
        anything is transferred, whatever kind the link claimed to be.
        """
        if ref.kind is ResourceKind.FILE and not inspect_files:
            return (self._job(ref, item),), ()
        inspection = provider.inspect(ref)
        if not inspection.availability.is_available:
            return (), (UnresolvedSource(ref.url, _availability_reason(inspection.availability)),)
        if inspection.kind is not ResourceKind.FOLDER:
            metadata = inspection.metadata
            name = None if metadata is None else metadata.name
            size = None if metadata is None else metadata.size
            return (self._job(ref, item, name=name, size=size),), ()
        return self._entries(inspection, item)

    def _entries(
        self, inspection: ResourceInspection, item: SourceItem
    ) -> tuple[tuple[DownloadJob, ...], tuple[UnresolvedSource, ...]]:
        """Return one job per file the inspected container holds."""
        jobs = tuple(
            self._job(entry.ref, item, name=entry.metadata.name, size=entry.metadata.size)
            for entry in inspection.entries
            if entry.metadata.kind is ResourceKind.FILE
        )
        if inspection.truncated:
            reason = "the folder holds more entries than were listed; raise max_entries"
            return jobs, (UnresolvedSource(inspection.ref.url, reason),)
        if not jobs:
            return (), (UnresolvedSource(inspection.ref.url, "the folder holds no files"),)
        return jobs, ()

    def _job(
        self,
        ref: ResourceRef,
        item: SourceItem,
        *,
        name: str | None = None,
        size: int | None = None,
    ) -> DownloadJob:
        """Return the job for *ref*, stamped with when it was planned."""
        return DownloadJob(
            ref=ref,
            origin=item.origin,
            name=name,
            size=size,
            discovered_at=self._clock(),
        )


def _availability_reason(availability: Availability) -> str:
    """Return a human-readable account of why nothing can be transferred.

    An undetermined verdict is worded differently from a determined one on
    purpose: "we could not find out" and "it is gone" lead to different next
    steps for whoever reads the report.
    """
    stated = availability.value.replace("_", " ")
    if availability.is_determined:
        return f"the provider reports it as {stated}"
    return f"no verdict could be obtained: {stated}"


def _utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)
