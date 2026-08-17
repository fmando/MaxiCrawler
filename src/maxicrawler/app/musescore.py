"""The worklist between a person's browser and the library.

MaxiCrawler cannot fetch from this host. Cloudflare answers a score page with
a challenge rather than the page, and answering one is a non-goal (VISION.md),
so the fetching is done where it has always worked: in a browser, by the person
whose subscription it is. What was never the tedious part is the clicking.

The tedious part is the bookkeeping over weeks — which twenty today, what
already arrived, what is still owed, and where a list of three hundred was left
off eleven days ago. That is what this owns:

* it turns score addresses into per-rendering lines of a worklist,
* it hands out a day's worth and no more,
* it notices what landed in the download folder,
* and it puts what landed into the library and crosses the line off.

**The download folder is read and never written to.** Nothing is moved,
renamed, or cleaned up there. A program that tidied somebody's Downloads folder
because it thought it recognised a file is a program nobody should run, and the
copy into the library costs a duplicate that a person can delete when they feel
like it.

**Matching is done only when it is certain.** One arrived PDF and one offered
PDF is an answer; two of each is a guess, and a guess here files music under
the wrong name in a library meant to be kept. Ambiguity is reported as
ambiguity so the page can ask.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, tzinfo
from pathlib import Path

from maxicrawler.config import Settings
from maxicrawler.database.musescore import (
    RequestState,
    ScoreRequest,
    SQLiteRequestQueue,
    StoredRequest,
)
from maxicrawler.database.sqlite import SQLiteDatabase
from maxicrawler.domain import (
    ContentDescriptor,
    DownloadStatus,
    ResourceKind,
    ResourceRef,
    UrlRecord,
)
from maxicrawler.downloader.errors import DownloadError
from maxicrawler.downloader.sink import LibrarySink
from maxicrawler.library import Library, resource_key
from maxicrawler.library.errors import LibraryError
from maxicrawler.library.records import ContentRecord, ResourceRecord, new_record
from maxicrawler.plugins.musescore import MuseScorePlugin, parse_score_url
from maxicrawler.providers.musescore import MUSESCORE_PROVIDER_NAME

READ_CHUNK = 1024 * 1024
"""How much of an arrived file is held at once while it is copied in."""


class WorklistError(RuntimeError):
    """Base class for everything a client of this service has to handle.

    Its own vocabulary rather than the downloader's and the library's, and that
    is a boundary rather than a preference: ``tests/test_api_boundaries.py``
    forbids the web interface from importing either package, and a handler that
    caught ``DownloadRefusedError`` would be importing one to name it. What
    reaches a client is what this service says, in words this service owns.
    """


class ArrivalRefusedError(WorklistError):
    """Raised when an arrived file could not be taken into the library.

    Under the size floor, unreadable, a disk that filled up. The cause is kept
    on the exception chain for whoever is debugging; the message is what a page
    can print.
    """


class OutsideDownloadsError(WorklistError):
    """Raised when a file offered as an arrival is not in the download folder.

    The interface has no authentication (ADR-025), and the page that settles a
    line names a file. Without this, that page is a way for anybody who can
    reach the port to copy any readable file on the machine into the library —
    a private key, a password store, ``/etc/shadow``. The folder the browser
    downloads into is the whole of what this feature ever needs to read, so it
    is the whole of what it may read.

    Enforced here rather than in the route, because the service is what owns
    the folder. A second client would otherwise have to remember the same rule.
    """


@dataclass(frozen=True, slots=True)
class Budget:
    """What is left of one day's allowance."""

    day: str
    limit: int
    spent: int

    @property
    def remaining(self) -> int:
        """Return how many more files may be taken today, never below zero."""
        return max(0, self.limit - self.spent)

    @property
    def exhausted(self) -> bool:
        """Return whether today is done."""
        return self.remaining == 0


@dataclass(frozen=True, slots=True)
class Arrival:
    """A file in the download folder that could belong to the worklist."""

    path: Path
    format: str
    size: int
    modified_at: datetime

    @property
    def stem(self) -> str:
        """Return the file's name without its extension."""
        return self.path.stem


@dataclass(frozen=True, slots=True)
class Today:
    """What there is to do today, and what it is measured against."""

    budget: Budget
    offered: tuple[StoredRequest, ...]
    waiting: int
    returned: int
    """How many of an earlier day's offers came back to the backlog."""


@dataclass(frozen=True, slots=True)
class Match:
    """One arrived file paired with the line it settles, when that is certain."""

    arrival: Arrival
    request: StoredRequest | None
    reason: str = ""
    """Why no pairing was made, for a page that has to explain itself."""


def day_of(moment: datetime, *, reset_hour: int) -> str:
    """Return the allowance *moment* counts against, as an ISO date.

    A moment before the reset hour belongs to the day before, because that is
    what "the allowance has not come back yet" means. With a reset hour of
    midnight this is simply the date, which is the case that needs no thought.
    """
    return (moment - timedelta(hours=reset_hour)).date().isoformat()


class WorklistService:
    """Runs a MuseScore worklist a person works through in their browser."""

    def __init__(
        self,
        settings: Settings,
        queue: SQLiteRequestQueue | None = None,
        *,
        library: Library | None = None,
        plugin: MuseScorePlugin | None = None,
    ) -> None:
        self._settings = settings
        # Assembled here when nobody hands one in, because this package is the
        # composition root and its clients are not. The web interface is
        # forbidden from naming a database adapter at all, so a service that
        # required one would have to be built somewhere that may not build it.
        self._queue = (
            queue
            if queue is not None
            else SQLiteRequestQueue(SQLiteDatabase(settings.database_path))
        )
        self._library = library if library is not None else Library(settings.library_path)
        self._plugin = plugin if plugin is not None else MuseScorePlugin()

    def initialize(self) -> tuple[str, ...]:
        """Create the worklist's table, or bring an existing one up to date.

        Safe on every start, and it has to be: a backlog measured in weeks
        outlives the release that created it.
        """
        return self._queue.initialize()

    @property
    def settings(self) -> Settings:
        """Return the settings this service was built from."""
        return self._settings

    @property
    def downloads(self) -> Path:
        """Return the folder the browser is expected to put files in."""
        configured = self._settings.musescore_downloads.strip()
        return Path(configured) if configured else Path.home() / "Downloads"

    # --- building the list ---------------------------------------------------

    def add(self, urls: Iterable[str], *, now: datetime) -> tuple[StoredRequest, ...]:
        """Queue every rendering of every score among *urls*, and return the new ones.

        Anything that is not a score address is ignored rather than refused: the
        usual source is a saved page or a pasted list, and complaining about the
        forty other links on it would be complaining about normal input.
        """
        requests: list[ScoreRequest] = []
        seen: set[tuple[str, str]] = set()
        for url in urls:
            link = parse_score_url(url)
            if link is None:
                continue
            for rendering in self._settings.musescore_formats:
                identity = (link.score_id, rendering)
                if identity in seen:
                    continue
                seen.add(identity)
                requests.append(
                    ScoreRequest(score_id=link.score_id, format=rendering, score_url=link.url)
                )
        return self._queue.add(requests, now=now)

    def recognises(self, url: str) -> bool:
        """Return whether *url* is a score this worklist can hold."""
        return self._plugin.can_handle(UrlRecord(raw_url=url, normalized_url=url))

    # --- the day -------------------------------------------------------------

    def budget(self, *, now: datetime) -> Budget:
        """Return what is left of the allowance for the day *now* falls in."""
        day = day_of(now, reset_hour=self._settings.musescore_reset_hour)
        return Budget(
            day=day, limit=self._settings.musescore_daily_limit, spent=self._queue.spent_on(day)
        )

    def today(self, *, now: datetime) -> Today:
        """Return today's list, filling it up to what the allowance still permits.

        Yesterday's unclaimed offers come back first. Then the list is topped
        up: a person who did twelve of twenty yesterday should find eight more
        this morning, not a list that has silently shrunk.
        """
        budget = self.budget(now=now)
        returned = self._queue.withdraw_offers(before_day=self._start_of(budget.day))
        already = self._queue.by_state(RequestState.OFFERED)
        room = max(0, budget.remaining - len(already))
        fresh = self._queue.offer(room, now=now)
        return Today(
            budget=budget,
            offered=already + fresh,
            waiting=self._queue.counts()[RequestState.WAITING],
            returned=returned,
        )

    # --- what arrived --------------------------------------------------------

    def arrivals(self, *, since: datetime | None = None) -> tuple[Arrival, ...]:
        """Return the files in the download folder this worklist could be about.

        Filtered to the renderings that are wanted, because a folder holds a
        person's whole life and almost none of it is sheet music. A missing
        folder yields nothing rather than raising: not having downloaded
        anything yet is not a fault.
        """
        folder = self.downloads
        wanted = {f".{rendering.lower()}" for rendering in self._settings.musescore_formats}
        found: list[Arrival] = []
        try:
            candidates = sorted(folder.iterdir())
        except OSError:
            return ()
        for path in candidates:
            if path.suffix.lower() not in wanted:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if not path.is_file():
                continue
            modified = datetime.fromtimestamp(stat.st_mtime, tz=_zone(since))
            if since is not None and modified < since:
                continue
            found.append(
                Arrival(
                    path=path,
                    format=path.suffix.lower().lstrip("."),
                    size=stat.st_size,
                    modified_at=modified,
                )
            )
        return tuple(found)

    def match(
        self, arrivals: Sequence[Arrival], offered: Sequence[StoredRequest]
    ) -> tuple[Match, ...]:
        """Pair each arrival with the line it settles, where that is not a guess.

        Three rules, tried in order. A file whose name is the title of exactly
        one offered line is that line. Otherwise, if one arrival and one offer
        of a rendering are alone together, they belong to each other. Anything
        else is reported unmatched with the reason, because filing music under
        the wrong name in a library meant to be kept is worse than asking.
        """
        remaining = list(offered)
        matched: list[Match] = []
        for arrival in arrivals:
            by_title = [
                request
                for request in remaining
                if request.format == arrival.format
                and request.title
                and request.title.casefold() == arrival.stem.casefold()
            ]
            if len(by_title) == 1:
                remaining.remove(by_title[0])
                matched.append(Match(arrival=arrival, request=by_title[0]))
                continue
            same_format = [request for request in remaining if request.format == arrival.format]
            if len(same_format) == 1:
                remaining.remove(same_format[0])
                matched.append(Match(arrival=arrival, request=same_format[0]))
                continue
            reason = (
                f"no {arrival.format} is waiting for a file"
                if not same_format
                else f"{len(same_format)} lines could be this {arrival.format}"
            )
            matched.append(Match(arrival=arrival, request=None, reason=reason))
        return tuple(matched)

    # --- crossing a line off -------------------------------------------------

    def store(self, request_id: str, path: Path, *, now: datetime) -> StoredRequest | None:
        """Copy *path* into the library and mark the request as arrived.

        Returns ``None`` when the request is unknown or already settled, which
        is what makes scanning the folder twice harmless: one file must never
        spend two days of an allowance.

        The file is **copied**. What the browser downloaded stays where the
        browser put it.

        Raises:
            OutsideDownloadsError: *path* is not in the download folder.
            ArrivalRefusedError: the file could not be taken into the library.
        """
        self.require_arrival(path)
        request = self._queue.request(request_id)
        if request is None or request.state in (RequestState.STORED, RequestState.DROPPED):
            return None
        ref = self.reference(request)
        key = resource_key(ref)
        entry = self._library.entry(ref)
        filename = f"{request.title or request.score_id}.{request.format}"
        # Translated rather than propagated. A client of this service must not
        # have to name a downloader exception to handle one, and the web
        # interface is forbidden from importing that package at all.
        try:
            with LibrarySink(entry, minimum_size=self._settings.min_download_size) as sink:
                sink.begin(ContentDescriptor(name=filename, size=path.stat().st_size))
                with path.open("rb") as handle:
                    while chunk := handle.read(READ_CHUNK):
                        sink.write(chunk)
                content = sink.commit()
        except (DownloadError, LibraryError, OSError) as error:
            raise ArrivalRefusedError(str(error)) from error
        entry.write(_record_for(ref, key, name=filename, now=now, content=content, request=request))
        return self._queue.mark_stored(
            request_id,
            now=now,
            day=day_of(now, reset_hour=self._settings.musescore_reset_hour),
            entry_key=key,
        )

    def drop(self, request_id: str, *, now: datetime, note: str = "") -> StoredRequest | None:
        """Take a line off the list without spending anything on it."""
        return self._queue.drop(request_id, now=now, note=note)

    def require_arrival(self, path: Path) -> Path:
        """Return *path* if it is a file the download folder actually holds.

        Resolved on both sides before comparing, so a path walking out through
        ``..`` or arriving by way of a symlink is judged by where it *lands*
        rather than by how it was spelled. A folder that does not exist admits
        nothing, which is the right answer rather than an awkward one.

        Raises:
            OutsideDownloadsError: *path* is somewhere else, or is not a file.
        """
        folder = self.downloads.resolve()
        try:
            candidate = path.resolve()
        except OSError as error:
            msg = f"not a readable file: {path}"
            raise OutsideDownloadsError(msg) from error
        if not candidate.is_file() or folder not in candidate.parents:
            msg = f"not a file in the download folder: {path}"
            raise OutsideDownloadsError(msg)
        return candidate

    def reference(self, request: StoredRequest) -> ResourceRef:
        """Return where in the library *request* belongs.

        Built the same way the provider would have built it, so a file that
        arrived by hand today and one fetched automatically some future day
        land in the same place rather than twice.
        """
        return ResourceRef(
            provider=MUSESCORE_PROVIDER_NAME,
            resource_id=f"{request.title or request.score_id}.{request.format}",
            kind=ResourceKind.FILE,
            url=request.score_url,
            parent_id=request.score_id,
        )

    def _start_of(self, day: str) -> str:
        """Return the timestamp an offer must be at or after to count as today's."""
        moment = datetime.fromisoformat(day) + timedelta(hours=self._settings.musescore_reset_hour)
        return moment.isoformat()


def _zone(since: datetime | None) -> tzinfo | None:
    """Return the timezone to read file times in, matching *since* when given.

    A naive cutoff and an aware file time cannot be compared, and the caller's
    cutoff is the one that decides which of the two this is.
    """
    return None if since is None else since.tzinfo


def _record_for(
    ref: ResourceRef,
    key: str,
    *,
    name: str,
    now: datetime,
    content: ContentRecord,
    request: StoredRequest,
) -> ResourceRecord:
    """Return the library document describing a file that arrived by hand.

    Deliberately indistinguishable from one a transfer would have written,
    apart from being true: it was downloaded, it is complete, and it took one
    attempt. Marking it as somehow lesser would make the library two libraries.
    """
    record = new_record(ref, key, status=DownloadStatus.COMPLETED, name=name)
    return replace(
        record,
        downloaded_at=now,
        attempts=1,
        content=content,
        discovered_at=request.added_at,
    )
