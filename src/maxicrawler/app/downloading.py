"""Downloading a share link, for whoever is asking.

The command line built this graph inline for as long as it was the only client
that downloaded anything — a provider registry, a library, a manager, three
lines of wiring. :class:`~maxicrawler.app.crawling.CrawlService` already exists
because that arrangement produced two crawlers the moment a second client
appeared; this module is the same argument applied to the second half of the
chain, before rather than after the fact.

Three properties are worth stating before the code.

**Nothing here executes a download.** The manager in
:mod:`maxicrawler.downloader` does, unchanged, and this is the composition point
that hands it a registry, a library and a reporter. If a decision looks like it
belongs to *how* downloads run, it belongs down there instead.

**A client never sees a download-layer type.** :class:`DownloadProgress` and
:class:`DownloadSummary` are the whole vocabulary a caller needs, which is what
lets the web interface show a transfer without importing ``downloader``,
``providers`` or ``library`` — the boundary ``tests/test_api_boundaries.py``
reads rather than believes.

The one exception is :class:`~maxicrawler.downloader.control.DownloadControl`,
and it is re-exported here rather than reached for. A client that offers a Stop
button needs the handle *before* the transfer starts, so there is nothing this
layer could hand back instead; what it can do is make sure the interface still
imports from one place. It is a handle, not a result — nothing about how a
download runs travels with it.

**Reading the library is somebody else's job.**
:class:`~maxicrawler.app.library.LibraryService` browses what is stored; this
service puts things there. Two questions about one store, kept apart so that
neither grows the other's vocabulary.

**A browser may name a URL, never a path.**
:meth:`DownloadService.download` refuses anything that is not an absolute
HTTP(S) URL. The resolver underneath happily reads a file or a whole directory
of documents, which is right for a command line and would be a way to make the
server read its own disk on somebody else's click.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from maxicrawler.config import Settings
from maxicrawler.domain import DownloadStatus, ProviderCapability, UrlRecord
from maxicrawler.downloader import (
    DownloadControl,
    DownloadJob,
    DownloadManager,
    DownloadOutcome,
    DownloadPlan,
    DownloadReport,
    NullProgressReporter,
    ProgressReporter,
    ResourceIdentity,
    looks_like_url,
)
from maxicrawler.library import Library, provider_directory, resource_key
from maxicrawler.plugins import PluginResolver, create_default_registry
from maxicrawler.providers import (
    ProviderRegistry,
    RetryPolicy,
    UrllibFileTransport,
    UrllibStreamTransport,
    UrllibTransport,
    create_default_provider_registry,
)
from maxicrawler.utils import normalize_url, strip_fragment
from maxicrawler.utils.addresses import PrivateNetworkRule

ProgressListener = Callable[["DownloadProgress"], None]
"""Called on the thread performing the transfer, so it must not block.

The same contract :class:`~maxicrawler.api.jobs.CrawlJob` listeners are under,
and for the same reason: whatever this hands its value to, it hands it and
returns.
"""

__all__ = [
    "NOTHING_TO_DOWNLOAD",
    "DownloadControl",
    "DownloadProgress",
    "DownloadService",
    "DownloadSummary",
    "ProgressListener",
]

NOTHING_TO_DOWNLOAD = "the link led to nothing that can be downloaded"
"""Said when a source produced no jobs and named no reason of its own."""


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    """What is true about one download while it runs.

    ``bytes_written`` and ``files_finished`` are totals for the whole request,
    not for the file currently moving, so a client can render one bar however
    many resources the link turned out to hold. ``label`` is the resource being
    transferred right now, which is the only part that changes underneath them.
    """

    label: str
    status: DownloadStatus
    bytes_written: int = 0
    total_bytes: int | None = None
    """What the plan expects in total, or ``None`` when something is unknown."""

    files_total: int = 1
    files_finished: int = 0
    reason: str | None = None

    @property
    def fraction(self) -> float | None:
        """Return how far along this is between 0 and 1, if that is knowable.

        ``None`` rather than zero when no total is known: a bar that sits at
        the start for two minutes claims progress it cannot see, and a client
        that gets ``None`` can say so instead.
        """
        if self.total_bytes is None or self.total_bytes <= 0:
            return None
        return min(1.0, self.bytes_written / self.total_bytes)


@dataclass(frozen=True, slots=True)
class DownloadSummary:
    """What became of one requested download.

    ``url`` is the share link with its fragment removed, so a summary is safe to
    render, log, and hand to a template. The credential that may have travelled
    in that fragment is used by the provider and appears in nothing here.
    """

    url: str
    status: DownloadStatus
    label: str
    bytes_written: int = 0
    total_bytes: int | None = None
    files_total: int = 0
    files_completed: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    path: Path | None = None
    """Where the payload landed, when exactly one resource was transferred."""

    directory: str | None = None
    key: str | None = None
    """How the library addresses the one resource this fetched, if it was one.

    Derived from the reference by the same two pure functions that decided the
    directory names in the first place, so a finished download can link straight
    to its own page in the library rather than to a list to search through.
    """

    reason: str | None = None
    library_root: Path | None = None

    @property
    def succeeded(self) -> bool:
        """Return whether the library holds what was asked for."""
        return self.status.is_success


class DownloadService:
    """Everything a client needs to download a link, and nothing about showing it."""

    def __init__(
        self,
        settings: Settings,
        *,
        providers: ProviderRegistry | None = None,
        library: Library | None = None,
    ) -> None:
        self._settings = settings
        self._injected_providers = providers
        self._injected_library = library
        self._cached_providers: ProviderRegistry | None = None
        self._resolver = PluginResolver(create_default_registry())

    @property
    def settings(self) -> Settings:
        """Return the settings every download of this service is built from."""
        return self._settings

    @property
    def library_root(self) -> Path:
        """Return where downloads are stored."""
        return self._library().root

    def build_manager(
        self,
        *,
        output: Path | None = None,
        reporter: ProgressReporter | None = None,
        max_entries: int | None = None,
        control: DownloadControl | None = None,
    ) -> DownloadManager:
        """Return a download manager wired from the configuration.

        Exposed beside :meth:`download` because a client that wants the two
        halves apart — ``--dry-run`` plans without running — needs the manager
        that will do both, not two of them.
        """
        return DownloadManager(
            self._providers(max_entries=max_entries),
            self._library(output),
            reporter=reporter if reporter is not None else NullProgressReporter(),
            control=control,
        )

    def download(
        self,
        url: str,
        *,
        on_progress: ProgressListener | None = None,
        control: DownloadControl | None = None,
    ) -> DownloadSummary:
        """Download exactly what *url* points at, and report what happened.

        One link in, one account out. A dead share, a host nobody supports, a
        transfer that broke halfway — all of them come back as a summary with a
        reason, because they are findings rather than faults on our side.

        The link is inspected before anything is transferred, so the summary and
        every progress report carry the resource's name and size rather than its
        handle. That costs one request and is what a deliberate single download
        can afford; a run over a document full of links deliberately does not.

        A *control* makes the transfer stoppable. It is honoured on every chunk,
        so a stop takes effect within one chunk rather than at the end of the
        file, and the library is left exactly as it was.

        Raises:
            ValueError: *url* is not an absolute HTTP(S) URL; see
                :meth:`require_url`.
        """
        target = self.require_url(url)
        reporter = _ListenerReporter(on_progress)
        manager = self.build_manager(reporter=reporter, control=control)
        plan = manager.plan(target, inspect_files=True)
        reporter.observe(plan)
        return _summarize(manager.run(plan), url=strip_fragment(target))

    @staticmethod
    def require_url(url: str) -> str:
        """Return *url* stripped of surrounding space, if it may be downloaded.

        The one rule a client cannot be trusted to apply for itself, so it is
        applied here and nowhere else: a source must be an absolute HTTP(S) URL.
        :class:`~maxicrawler.downloader.sources.SourceResolver` treats anything
        else as a path and reads a file or a whole directory of documents for
        the links inside — right for a command line, and a way to make a server
        read its own disk on somebody else's click.

        Separate from :meth:`download` so a caller that starts transfers on a
        worker thread can refuse a bad URL before it starts one.

        Raises:
            ValueError: *url* is not an absolute HTTP(S) URL. The message names
                the URL without its fragment, because a rejected link is echoed
                back to whoever sent it.
        """
        target = url.strip()
        if not looks_like_url(target):
            msg = f"not an absolute HTTP(S) URL: {strip_fragment(target) or target}"
            raise ValueError(msg)
        return target

    def downloadable(self, urls: Iterable[str]) -> frozenset[str]:
        """Return which of *urls* some provider here could transfer.

        Answered from the URL string and a declared capability alone: a plugin
        classifies, a provider claims the classification, and the provider says
        whether it was composed with everything a transfer needs. No request is
        made, which is what lets a report of two hundred links ask this about
        all of them while it renders.
        """
        registry = self._providers()
        return frozenset(url for url in urls if self._can_download(url, registry))

    def can_download(self, url: str) -> bool:
        """Return whether *url* is a link this installation could fetch."""
        return self._can_download(url, self._providers())

    def _can_download(self, url: str, registry: ProviderRegistry) -> bool:
        """Return whether *registry* holds a provider that could fetch *url*."""
        try:
            record = UrlRecord(raw_url=url, normalized_url=normalize_url(url))
        except ValueError:
            return False
        classification = self._resolver.resolve(record).classification
        if classification is None:
            return False
        provider = registry.resolve(classification)
        return provider is not None and provider.metadata.supports(ProviderCapability.DOWNLOAD)

    def _library(self, output: Path | None = None) -> Library:
        """Return the library to store into, *output* overriding everything."""
        if output is not None:
            return Library(output)
        if self._injected_library is not None:
            return self._injected_library
        return Library(self._settings.library_path)

    def _providers(self, *, max_entries: int | None = None) -> ProviderRegistry:
        """Return the providers, wired to the configured network behaviour.

        Always with a stream transport: this service exists to move content, and
        a provider that cannot say :attr:`ProviderCapability.DOWNLOAD` would make
        :meth:`downloadable` answer no to everything. The default registry is
        built once and reused; a caller naming its own ``max_entries`` gets its
        own, because that number is baked into the provider.

        The file transport carries the private-network rule built from the same
        settings the crawler's guard is built from — the *only* transport here
        that can be pointed at any host, because it is the only one that serves
        URLs somebody else wrote. Mega's talks to mega.nz and nowhere else.

        ``direct_downloads`` withholds that transport rather than removing the
        provider, so an installation that says no to fetching arbitrary files
        gets a provider advertising no capability instead of a registry with a
        hole in it. Everything that asks *"can this be downloaded?"* is
        answered the same way either way.
        """
        if self._injected_providers is not None:
            return self._injected_providers
        if max_entries is None and self._cached_providers is not None:
            return self._cached_providers
        settings = self._settings
        registry = create_default_provider_registry(
            transport=UrllibTransport(
                user_agent=settings.user_agent, timeout=settings.network_timeout
            ),
            stream=UrllibStreamTransport(
                user_agent=settings.user_agent, timeout=settings.network_timeout
            ),
            files=UrllibFileTransport(
                user_agent=settings.user_agent,
                timeout=settings.network_timeout,
                max_redirects=settings.max_redirects,
                rule=PrivateNetworkRule(
                    allow=settings.private_network_allowlist,
                    allow_private=settings.allow_private_networks,
                ),
            )
            if settings.direct_downloads
            else None,
            retry=RetryPolicy(max_attempts=settings.network_retries),
            max_entries=max_entries if max_entries is not None else settings.max_entries,
        )
        if max_entries is None:
            self._cached_providers = registry
        return registry


class _ListenerReporter:
    """Turns the download layer's reporting into one listener callback.

    Satisfies :class:`~maxicrawler.downloader.progress.ProgressReporter`
    structurally, which is the whole point: it is the single adapter that keeps
    :class:`DownloadJob` and :class:`DownloadOutcome` from crossing into a
    client. Every method runs on the thread performing the transfer, one job at
    a time, so the counters need no lock.

    Byte counts arrive as *totals for one job*, so the running total is the sum
    over the jobs seen rather than an accumulation of deltas. A retried or
    re-reported job therefore corrects itself instead of counting twice.
    """

    __slots__ = (
        "_files_finished",
        "_files_total",
        "_label",
        "_listener",
        "_total_bytes",
        "_written",
    )

    def __init__(self, listener: ProgressListener | None) -> None:
        self._listener = listener
        self._label = ""
        self._files_total = 1
        self._files_finished = 0
        self._total_bytes: int | None = None
        self._written: dict[ResourceIdentity, int] = {}

    def observe(self, plan: DownloadPlan) -> None:
        """Take the shape of the run from *plan*, before it starts."""
        self._files_total = len(plan.jobs)
        self._total_bytes = plan.total_size

    def begin(self) -> None:
        """Do nothing; the first transfer is what a reader wants to hear about."""

    def end(self) -> None:
        """Do nothing; the summary is the end of the run."""

    def started(self, job: DownloadJob, size: int | None) -> None:
        """Announce which resource is moving now."""
        self._label = job.label
        self._emit(DownloadStatus.RUNNING)

    def advanced(self, job: DownloadJob, written: int) -> None:
        """Record that *job* has received *written* bytes in total."""
        self._written[job.identity] = written
        self._emit(DownloadStatus.RUNNING)

    def finished(self, job: DownloadJob, outcome: DownloadOutcome) -> None:
        """Record the verdict for *job* and count it as done."""
        self._written[job.identity] = outcome.bytes_written
        self._files_finished += 1
        self._label = outcome.label
        self._emit(outcome.status, reason=outcome.reason)

    def _emit(self, status: DownloadStatus, *, reason: str | None = None) -> None:
        """Hand the current state to the listener, if there is one."""
        if self._listener is None:
            return
        self._listener(
            DownloadProgress(
                label=self._label,
                status=status,
                bytes_written=sum(self._written.values()),
                total_bytes=self._total_bytes,
                files_total=self._files_total,
                files_finished=self._files_finished,
                reason=reason,
            )
        )


def _summarize(report: DownloadReport, *, url: str) -> DownloadSummary:
    """Return the account of *report* as one request's outcome.

    A link that turned out to be a folder is one request holding several
    transfers, so the counts are plural and the status is the verdict over all
    of them: anything failed makes the request failed, anything stopped makes it
    stopped, everything skipped makes it skipped, and the rest is a completed
    download.

    Stopped outranks completed but not failed. A folder whose third file broke
    and whose fourth was cancelled did both, and the one worth telling somebody
    about is the one they did not choose.
    """
    completed = report.completed
    skipped = report.skipped
    failed = report.failed
    cancelled = report.cancelled
    if not report.plan.jobs:
        reason = report.unresolved[0].reason if report.unresolved else NOTHING_TO_DOWNLOAD
        return DownloadSummary(
            url=url,
            status=DownloadStatus.FAILED,
            label=url,
            reason=reason,
            library_root=report.library_root,
        )
    outcomes = report.outcomes
    single = outcomes[0] if len(outcomes) == 1 else None
    status = DownloadStatus.COMPLETED
    if failed:
        status = DownloadStatus.FAILED
    elif cancelled:
        status = DownloadStatus.CANCELLED
    elif skipped and not completed:
        status = DownloadStatus.SKIPPED
    return DownloadSummary(
        url=url,
        status=status,
        label=single.label if single is not None else url,
        bytes_written=report.bytes_written,
        total_bytes=report.plan.total_size,
        files_total=len(report.plan.jobs),
        files_completed=len(completed),
        files_skipped=len(skipped),
        files_failed=len(failed),
        path=None if single is None else single.path,
        directory=None if single is None else provider_directory(single.job.ref.provider),
        key=None if single is None else resource_key(single.job.ref),
        reason=_reason(report),
        library_root=report.library_root,
    )


def _reason(report: DownloadReport) -> str | None:
    """Return the one line explaining a run that was not a plain success."""
    for outcome in report.failed:
        return outcome.reason
    for outcome in report.cancelled:
        return outcome.reason
    if report.unresolved:
        return report.unresolved[0].reason
    for outcome in report.skipped:
        return outcome.reason
    return None
