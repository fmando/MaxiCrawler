"""Turning a crawl into something a template can render.

Every function here is pure. Nothing touches HTTP, a database or a socket, so
the whole presentation layer is testable the way
:func:`maxicrawler.cli.crawling.render_crawl` is: give it a value, read the
result.

Templates receive plain dicts and tuples rather than domain objects. That is
not ceremony — it is what keeps a template from quietly growing logic. A
template that can only read strings and numbers cannot decide anything, so
every decision stays here where a test can reach it.

**Wording lives here, not in a shared module.** The terminal and a browser
legitimately say things differently: `render_crawl` writes "stopped at the page
limit" on its own line, while a page shows a short badge. Sharing the *numbers*
is what matters, and those come from the same report either way.
"""

from collections.abc import Callable, Container, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from urllib.parse import urlencode

from maxicrawler.api.downloads import DownloadSnapshot, QueueSnapshot, QueueTally
from maxicrawler.api.jobs import JobSnapshot
from maxicrawler.app import (
    DEFAULT_PER_PAGE,
    UNTRACKED,
    DownloadProgress,
    DownloadSummary,
    LibraryFacet,
    LibraryItem,
    LibraryPage,
    LibraryPlace,
    LibraryQuery,
    LibrarySort,
    LinkItem,
    LinkPage,
    LinkQuery,
    LinkSort,
    LinkState,
    PageQuery,
    PageSlice,
    PageState,
    Preview,
    PreviewShape,
    StoredPayload,
    TargetKind,
)
from maxicrawler.app.maintenance import MaintenanceRun, Toolbox
from maxicrawler.app.thumbnails import CacheUsage
from maxicrawler.app.viewing import MediaKind
from maxicrawler.config import Settings
from maxicrawler.crawler import PluginUsage
from maxicrawler.database import StoredCrawl
from maxicrawler.domain import DownloadStatus, ReviewVerdict
from maxicrawler.plugins.generic import GENERIC_PLUGIN_NAME
from maxicrawler.utils import elide_middle, format_size, strip_fragment
from maxicrawler.web.models import LinkKind
from maxicrawler.web.report import CrawlReport, PageOutcome, SkipReason
from maxicrawler.web.session import CrawlOptions, CrawlState

ABANDONED_LABEL = "abandoned"
"""What a crawl the database left unfinished and nobody is running is called.

Not a :class:`~maxicrawler.web.session.CrawlState`, because nothing ever
*enters* this state: it is what an unfinished record means once you know no
process is behind it. The engine cannot write it, since a process that is being
killed does not get to update a row on its way out.
"""

STATE_LABELS: dict[CrawlState, str] = {
    CrawlState.PENDING: "queued",
    CrawlState.RUNNING: "running",
    CrawlState.COMPLETED: "completed",
    CrawlState.PAGE_LIMIT: "page limit",
    CrawlState.INTERRUPTED: "stopped",
}
"""Short enough to sit in a badge; the CLI says the same things at more length."""

STATE_TONES: dict[CrawlState, str] = {
    CrawlState.PENDING: "idle",
    CrawlState.RUNNING: "busy",
    CrawlState.COMPLETED: "good",
    CrawlState.PAGE_LIMIT: "warn",
    CrawlState.INTERRUPTED: "warn",
}
"""Which of the four style classes a state gets. Colour is decided in CSS."""

MAX_LISTED_DOWNLOADS = 200
"""How many stored resources the library page lists before saying the rest."""

STATUS_LABELS: dict[DownloadStatus, str] = {
    DownloadStatus.PENDING: "starting",
    DownloadStatus.RUNNING: "downloading",
    DownloadStatus.COMPLETED: "completed",
    DownloadStatus.SKIPPED: "already stored",
    DownloadStatus.REFUSED: "not kept",
    DownloadStatus.FAILED: "failed",
    DownloadStatus.CANCELLED: "stopped",
}
"""A skipped download is not a lesser success; it is one that needed no bytes.

A stopped one is not a failure either. The person reading that word is the
person who clicked the button, and calling their decision an error is how an
interface teaches somebody to distrust it.

"Not kept" for a refusal, rather than "too small": the state is the general one
— a limit here declined this — and which limit it was belongs in the reason
beside it, where the numbers are. A label naming today's only rule would have to
be rewritten by the next one.
"""

STATUS_TONES: dict[DownloadStatus, str] = {
    DownloadStatus.PENDING: "idle",
    DownloadStatus.RUNNING: "busy",
    DownloadStatus.COMPLETED: "good",
    DownloadStatus.SKIPPED: "good",
    DownloadStatus.REFUSED: "idle",
    DownloadStatus.FAILED: "bad",
    DownloadStatus.CANCELLED: "idle",
}

KIND_WORDS: dict[MediaKind, str] = {
    MediaKind.IMAGE: "images",
    MediaKind.VIDEO: "video",
    MediaKind.AUDIO: "audio",
    MediaKind.PDF: "PDF",
    MediaKind.DOCUMENT: "documents",
    MediaKind.ARCHIVE: "archives",
    MediaKind.TEXT: "text",
    MediaKind.OTHER: "other",
}
"""What each file category is called where somebody picks one.

Plural, because every one of them names a filter rather than a file: the control
answers "show me the images", not "this is an image".
"""

KIND_LABELS: dict[LinkKind, str] = {
    LinkKind.ANCHOR: "anchor",
    LinkKind.IMAGE: "image",
    LinkKind.SCRIPT: "script",
    LinkKind.STYLESHEET: "stylesheet",
    LinkKind.FRAME: "frame",
    LinkKind.REDIRECT: "meta refresh",
    LinkKind.TEXT: "plain text",
}


def format_number(value: int) -> str:
    """Return *value* with thousands separated, because these get large."""
    return f"{value:,}"


def format_duration(seconds: float) -> str:
    """Return a readable length of time.

    Seconds below a minute, because that is where most crawls live; minutes and
    hours above it, because "412.7 s" is a number nobody converts in their head.
    """
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, remaining = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes} min {remaining:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def format_timestamp(moment: datetime) -> str:
    """Return a moment as a person reading a table wants it: short and sortable."""
    return moment.strftime("%Y-%m-%d %H:%M")


def describe_scope(options: CrawlOptions) -> str:
    """Return what a crawl was allowed to reach, in one phrase.

    The phrase *is* the scope value. Working the precedence out here as well
    would be a second opinion on the same three booleans, and the day they
    disagree is the day a report says one thing and the crawl did another.
    """
    return str(options.scope)


def describe_options(options: CrawlOptions) -> str:
    """Return the one line that says what a crawl was told to do.

    robots.txt is stated either way rather than only when it was ignored.
    "Did this crawl obey robots.txt" is a question asked of a run that finished
    months ago, and silence would answer it only for somebody who already knew
    the default — which is exactly the person who would not be asking.
    """
    return (
        f"depth {options.max_depth} · {describe_scope(options)} · "
        f"max {format_number(options.max_pages)} pages · "
        f"robots.txt {'obeyed' if options.respect_robots else 'ignored'}"
    )


@dataclass(frozen=True, slots=True)
class PluginShare:
    """How much of a crawl's discovery one plugin accounted for."""

    name: str
    count: int
    share: float
    """Between 0 and 1, of every classified URL."""

    is_fallback: bool
    """Whether this is the generic plugin rather than a host-specific one."""

    @property
    def percent(self) -> str:
        """Return the share as a rounded percentage."""
        return f"{self.share * 100:.0f}%"

    @property
    def count_label(self) -> str:
        """Return the count as the page prints it."""
        return format_number(self.count)

    @property
    def width(self) -> str:
        """Return the bar width as a CSS percentage.

        Not rounded to whole percent like the label: a plugin accounting for
        four URLs in ten thousand would otherwise draw a bar of nothing, and a
        bar that is invisible says "none" where the table says "4".
        """
        return f"{max(self.share * 100, 0.4):.2f}%" if self.count else "0%"


def plugin_shares(usage: Iterable[PluginUsage]) -> tuple[PluginShare, ...]:
    """Return the plugin distribution, the interesting plugins first.

    Host-specific plugins come before the generic fallback whatever the counts
    say. On a page full of share links the generic plugin will always win by
    volume, and burying "mega: 1291" underneath it would hide the one line this
    project exists to produce.
    """
    entries = tuple(usage)
    total = sum(entry.count for entry in entries)
    shares = [
        PluginShare(
            name=entry.name,
            count=entry.count,
            share=entry.count / total if total else 0.0,
            is_fallback=entry.name == GENERIC_PLUGIN_NAME,
        )
        for entry in entries
    ]
    shares.sort(key=lambda share: (share.is_fallback, -share.count, share.name))
    return tuple(shares)


def progress_view(snapshot: JobSnapshot) -> dict[str, Any]:
    """Return what a running crawl's page shows.

    Every count leaves here as a string a template can print unchanged. That is
    what lets the browser's live update write values into the page without
    formatting anything: the numbers it receives are the ones the server would
    have rendered.
    """
    return {
        "job_id": snapshot.job_id,
        "seed_url": snapshot.seed_url,
        "state": str(snapshot.state),
        "state_label": _state_label(snapshot),
        "state_tone": _state_tone(snapshot),
        "options": describe_options(snapshot.options),
        "max_pages": format_number(snapshot.options.max_pages),
        "pages_visited": format_number(snapshot.pages_visited),
        "pages_failed": format_number(snapshot.pages_failed),
        "pages_attempted": format_number(snapshot.pages_attempted),
        "links_found": format_number(snapshot.links_found),
        "latest_url": snapshot.latest_url,
        "elapsed": format_duration(snapshot.elapsed_seconds),
        "progress_percent": round(snapshot.progress * 100),
        "is_finished": snapshot.is_finished,
        "error": snapshot.error,
    }


QUEUED_LABEL = "waiting"
"""What a request that has not been picked up yet is called.

Not "starting", which is what :data:`STATUS_LABELS` says about
:attr:`DownloadStatus.PENDING`. Both are "nothing has been transferred", and
only one of them is somebody's turn to wait for.
"""


def download_view(
    snapshot: DownloadSnapshot, *, position: int | None = None, is_paused: bool = False
) -> dict[str, Any]:
    """Return what a download's page shows.

    Every value leaves here as a string a template prints unchanged, for the
    same reason :func:`progress_view` does: the live update writes the server's
    own formatting into the page rather than inventing its own.

    ``progress_percent`` is ``None`` when nothing stated a total. A bar that
    sits at zero for two minutes claims progress it cannot see, so the page
    shows an indeterminate one and says how much has arrived instead.
    """
    progress = snapshot.progress
    fraction = progress.fraction
    return {
        "download_id": snapshot.download_id,
        "url": snapshot.url,
        "label": snapshot.label,
        "status": str(snapshot.status),
        "state_label": QUEUED_LABEL if snapshot.is_queued else STATUS_LABELS[snapshot.status],
        "state_tone": "idle" if snapshot.is_queued else STATUS_TONES[snapshot.status],
        "is_queued": snapshot.is_queued,
        "is_running": snapshot.is_running,
        # Where in the line, counting from one, for a request that is in one.
        # `None` once it is being worked on, which is when the bar takes over
        # from the number as the thing worth looking at.
        "position": None if position is None else format_number(position),
        # A queue nobody is draining is why a request is not moving, and a page
        # that said "waiting" without saying that would be describing a stall.
        "queue_is_paused": is_paused,
        "bytes_written": format_size(progress.bytes_written),
        "total_bytes": None if progress.total_bytes is None else format_size(progress.total_bytes),
        "transferred": _transferred(progress.bytes_written, progress.total_bytes),
        "progress_percent": None if fraction is None else round(fraction * 100),
        "has_total": fraction is not None,
        "files_total": format_number(progress.files_total),
        "files_finished": format_number(progress.files_finished),
        "has_many_files": progress.files_total > 1,
        # Absent for a request that was never picked up. Its zero would
        # otherwise read as a transfer that took no time rather than one that
        # never happened.
        "elapsed": format_duration(snapshot.elapsed_seconds) if snapshot.was_started else None,
        "rate": _rate(progress.bytes_written, snapshot.elapsed_seconds),
        "remaining": _remaining(progress, snapshot),
        "is_finished": snapshot.is_finished,
        "succeeded": snapshot.summary is not None and snapshot.summary.succeeded,
        # Not the negation of `succeeded`, and that is the point of both flags.
        # A payload a configured limit turned away did not arrive and never
        # will: its reason is worth reading, and its "Try again" is a button
        # that could only refuse it again.
        "can_retry": snapshot.status.invites_retry,
        "was_refused": snapshot.status is DownloadStatus.REFUSED,
        "reason": snapshot.reason,
        "error": snapshot.error,
        "path": None if snapshot.path is None else snapshot.path.as_posix(),
        # Straight to the file rather than to a list to search through. Absent
        # when the request turned out to hold several files, which is when the
        # library itself is the right place to land.
        "item_url": _item_url(snapshot.summary),
    }


def queue_view(snapshot: QueueSnapshot, *, limit: int) -> dict[str, Any]:
    """Return what the queue page shows.

    Three lists and a set of counters. The running transfer is the only thing
    here rendered in full — it is the only one with something to report — so it
    goes through :func:`download_view` and everything else through a row
    builder that answers only what its table asks.

    There is a bar across the whole queue and deliberately no estimate under it.
    A waiting request has not been inspected: nothing here knows what it points
    at, how many files it will turn out to be or how large they are, so "about
    twelve minutes left" would be a number invented rather than measured. What
    can be measured is what has already happened, and that is what is shown.
    """
    waiting = tuple(
        _waiting_row(item, position, last=position == len(snapshot.waiting))
        for position, item in enumerate(snapshot.waiting, start=1)
    )
    unarrived = sum(1 for item in snapshot.finished if item.status.invites_retry)
    return {
        "is_paused": snapshot.is_paused,
        "is_busy": snapshot.is_busy,
        "follow": queue_follow(snapshot),
        "running": tuple(download_view(item) for item in snapshot.running),
        "running_count": format_number(len(snapshot.running)),
        "waiting": waiting,
        "finished": tuple(_finished_row(item) for item in snapshot.finished),
        "remaining": format_number(snapshot.remaining),
        "waiting_count": format_number(len(snapshot.waiting)),
        "finished_count": format_number(len(snapshot.finished)),
        "succeeded": format_number(snapshot.succeeded),
        "failed": format_number(snapshot.failed),
        "stopped": format_number(snapshot.stopped),
        "has_failures": snapshot.failed > 0,
        "bytes_written": format_size(snapshot.bytes_written),
        # How far along the whole queue is, which the per-file bar cannot say.
        "done": format_number(snapshot.done),
        "known": format_number(snapshot.known),
        "progress_percent": _queue_percent(snapshot),
        "has_progress": snapshot.known > 0,
        # An average over the time actually spent transferring, and said to be
        # one. It is not what the line is doing this second, and a page that let
        # it be read that way would be lying quietly rather than loudly.
        "rate": _rate(snapshot.bytes_written, snapshot.transfer_seconds),
        # Counted off the rows on the page rather than from the totals, because
        # this is the label on a button that acts on exactly those rows: the
        # ones already evicted have no URL left to try again.
        "unarrived": format_number(unarrived),
        # One, and the row's own button is beside it already. A second button
        # doing the same thing as the one next to it teaches nothing.
        "can_retry_all": unarrived > 1,
        "has_history": bool(snapshot.finished),
        # Named rather than implied, so a refusal is not the first time somebody
        # learns there is a ceiling at all.
        "limit": format_number(limit),
        "is_nearly_full": len(snapshot.waiting) >= limit * NEARLY_FULL,
    }


def _queue_percent(snapshot: QueueSnapshot) -> int:
    """Return how much of what is known has been got through, as a percentage.

    Rounded down, so a queue with anything left in it never reads as a hundred
    per cent. The last file finishing is what puts it there, not arithmetic.
    """
    if snapshot.known == 0:
        return 0
    return int(snapshot.done * 100 / snapshot.known)


NEARLY_FULL = 0.9
"""How full the queue gets before the page says so.

Late enough not to nag over an ordinary afternoon, early enough that a refusal
is not a surprise.
"""

QUEUE_PART = "queue"
"""What ``part`` has to say for the queue's panels to be answered on their own."""

QUEUE_FRAGMENT_URL = f"/downloads?part={QUEUE_PART}"
"""Where the panels of the queue page are, without the page around them."""

QUEUE_REGION = "queue"
"""The element on the queue page that holds everything a transfer changes."""


def queue_follow(snapshot: QueueSnapshot) -> dict[str, Any] | None:
    """Return what the queue page has left to watch, or ``None``.

    Three answers rather than two, and the third is what this exists for. There
    are transfers to listen to; there is nothing left to do at all; and there is
    the moment *between* two transfers, where the queue is busy and no worker
    has picked the next one up. A page that read the third as the second would
    stop following a batch of two hundred at whichever file lost that race —
    which over two hundred files is not a rare event, it is an expected one. So
    the third is answered with somewhere to ask again and nothing to listen to,
    and the browser asks again.

    A stream apiece rather than one for the queue as a whole. The alternative —
    asking for the panels on a timer — would re-render every waiting row on
    every tick, and there can be a thousand of them; a stream carries the
    numbers of one transfer and costs the page nothing else.

    A paused queue is not busy in the sense this means. Nothing will be taken
    off it until somebody presses Resume, and that press is a page load.

    Both URLs are written here rather than in the template because they are the
    same decision: *where the answer is* and *where it goes* belong to whoever
    decided there was something to ask for.
    """
    if not snapshot.is_busy or snapshot.is_paused:
        return None
    return {
        "streams": tuple(
            f"/downloads/{item.download_id}/events"
            for item in snapshot.running
            if not item.is_finished
        ),
        "swap": QUEUE_FRAGMENT_URL,
        "into": QUEUE_REGION,
    }


QUEUE_STRIP_URL = "/downloads"
"""Where the strip in the top bar leads. The queue is the only detail it has."""


def queue_strip(tally: QueueTally) -> dict[str, Any] | None:
    """Return the queue's line for the top bar, or ``None`` when it has none.

    Counts and nothing else. The line that names the running file already
    exists on the two pages that have room for it; this one is on *every* page,
    which is a different job and has to be a different sentence — otherwise the
    dashboard says the same thing twice in two shapes.

    A part is written only when it is not zero, so an idle installation has an
    unchanged top bar and a busy one gains exactly as much as it has to say.
    Read once per page render from :meth:`~maxicrawler.api.downloads.TransferQueue.tally`,
    which is why it is counts: a strip is not worth a snapshot of five hundred
    waiting requests.
    """
    if not tally.is_worth_saying:
        return None
    parts = []
    if tally.running:
        parts.append({"text": f"{format_number(tally.running)} downloading", "tone": "busy"})
    if tally.waiting:
        parts.append({"text": f"{format_number(tally.waiting)} waiting", "tone": "idle"})
    if tally.failed:
        parts.append({"text": f"{format_number(tally.failed)} failed", "tone": "bad"})
    if tally.is_paused:
        # Last, and said even with nothing in the queue: it is the answer to
        # "why is nothing happening", which is a question asked about an empty
        # queue as often as about a full one.
        parts.append({"text": "paused", "tone": "warn"})
    return {"parts": tuple(parts), "url": QUEUE_STRIP_URL}


def _waiting_row(snapshot: DownloadSnapshot, position: int, *, last: bool) -> dict[str, Any]:
    """Return one line of the waiting list.

    Whether a row can move is decided here rather than in the template: the
    first row has no "up" and the last has no "down", and a button that does
    nothing is worse than one that is not there.
    """
    return {
        "download_id": snapshot.download_id,
        "url": snapshot.url,
        "label": snapshot.label,
        "position": format_number(position),
        "can_move_up": position > 1,
        "can_move_down": not last,
    }


def _finished_row(snapshot: DownloadSnapshot) -> dict[str, Any]:
    """Return one line of the history."""
    return {
        "download_id": snapshot.download_id,
        "url": snapshot.url,
        "label": snapshot.label,
        "state_label": STATUS_LABELS[snapshot.status],
        "state_tone": STATUS_TONES[snapshot.status],
        "succeeded": snapshot.summary is not None and snapshot.summary.succeeded,
        # Not the negation of `succeeded`, and that is the point of both flags.
        # A payload a configured limit turned away did not arrive and never
        # will: its reason is worth reading, and its "Try again" is a button
        # that could only refuse it again.
        "can_retry": snapshot.status.invites_retry,
        "was_refused": snapshot.status is DownloadStatus.REFUSED,
        "reason": snapshot.reason,
        "transferred": format_size(snapshot.progress.bytes_written),
        "elapsed": format_duration(snapshot.elapsed_seconds) if snapshot.was_started else None,
        "item_url": _item_url(snapshot.summary),
    }


MINIMUM_TIMED_SECONDS = 0.5
"""How long a transfer must have run before its speed means anything.

Two chunks in the first fifty milliseconds divide out to a rate no line is going
to sustain, and a page that opened by claiming 400 MB/s would be lying twice —
once about the speed and once about the time remaining.
"""


def _rate(written: int, elapsed: float) -> str | None:
    """Return how fast a transfer is moving, or ``None`` while that is guesswork."""
    if written <= 0 or elapsed < MINIMUM_TIMED_SECONDS:
        return None
    return f"{format_size(int(written / elapsed))}/s"


def _remaining(progress: DownloadProgress, snapshot: DownloadSnapshot) -> str | None:
    """Return how much longer this will take, when that can be said at all.

    Needs three things nobody is owed: a total, a rate, and a transfer that has
    not finished. The estimate is the crudest possible — bytes left over bytes
    per second so far — and is offered as an estimate rather than dressed up,
    because a download over a link nobody controls is not predictable.
    """
    total = progress.total_bytes
    written = progress.bytes_written
    if snapshot.is_finished or total is None or written <= 0:
        return None
    if snapshot.elapsed_seconds < MINIMUM_TIMED_SECONDS or written >= total:
        return None
    return format_duration((total - written) / (written / snapshot.elapsed_seconds))


def _can_display(payload: StoredPayload | None) -> bool:
    """Return whether there is a file here that a browser may be shown."""
    return payload is not None and payload.media.can_display


def _item_url(summary: DownloadSummary | None) -> str | None:
    """Return the library page of the one file a download fetched, if it was one."""
    if summary is None or summary.directory is None or summary.key is None:
        return None
    return f"/library/{summary.directory}/{summary.key}"


VERDICT_WORDS: Mapping[ReviewVerdict, str] = MappingProxyType(
    {
        ReviewVerdict.UNREVIEWED: "unreviewed",
        ReviewVerdict.KEPT: "kept",
        ReviewVerdict.IGNORED: "ignored",
        ReviewVerdict.DISCARDED: "discarded",
    }
)
"""What each judgement is called where somebody filters by it."""

FAVOURITE_LABEL = "starred"
"""What the star is called in a filter, where it names a set rather than a file."""

FAVOURITE_MARK = "★"
UNFAVOURITE_MARK = "☆"
"""Filled and hollow, so the button says which way it would go by looking like it."""

VERDICT_BUTTONS: tuple[tuple[ReviewVerdict, str, str], ...] = (
    (ReviewVerdict.KEPT, "Keep", "Worth having"),
    (ReviewVerdict.IGNORED, "Ignore", "Not interesting, but leave the file alone"),
    (ReviewVerdict.DISCARDED, "Discard", "Delete the file and stop offering it"),
)
"""The judgements a button offers, and what each one promises.

Each hint says what happens rather than what it is called, and the third one has
to: "Discard" and "Ignore" are near-synonyms in English and are not near
anything in what they do. One leaves the file alone, the other deletes it.

"Unreviewed" is missing, and differently: it is not a judgement somebody passes
but the absence of one, so it is offered as *undo* on an entry that has already
been judged rather than as a fourth opinion. Taking a discard back is that same
undo — it does not bring the file back, and says so where it is offered.
"""


VERDICT_CHOICES: tuple[Mapping[str, str], ...] = tuple(
    MappingProxyType({"verdict": str(value), "label": label, "hint": hint})
    for value, label, hint in VERDICT_BUTTONS
)
"""The buttons, as a template reads them. Built once; the same on every row."""


class LibraryLayout(StrEnum):
    """Which way the library is laid out. Lives in the URL and nowhere else.

    Not a cookie and not a session: a bookmark of a filtered grid should be a
    filtered grid when it is opened, and the two ways of looking at the same
    listing should be two addresses. It is also not part of
    :class:`~maxicrawler.app.LibraryQuery` — the service answers the same
    question either way, and a field it never reads would be a field somebody
    later assumes it does.
    """

    GRID = "grid"
    LIST = "list"

    @classmethod
    def parse(cls, value: str | None) -> "LibraryLayout":
        """Return the layout *value* names, defaulting to the grid.

        Lenient like every other query parameter: a stale bookmark gets the
        default rather than a refusal.
        """
        try:
            return cls(value or "")
        except ValueError:
            return cls.GRID


LAYOUT_WORDS: Mapping[LibraryLayout, str] = MappingProxyType(
    {LibraryLayout.GRID: "Tiles", LibraryLayout.LIST: "List"}
)
"""What each layout is called where somebody switches between them."""

TILE_NAME_LENGTH = 34
"""How much of a file name a tile shows before eliding the middle of it."""

PREVIEW_ROUTES: Mapping[PreviewShape, Callable[[str], str]] = MappingProxyType(
    {
        PreviewShape.THUMBNAIL: lambda base: f"{base}/thumb",
        PreviewShape.IMAGE: lambda base: f"{base}/view",
    }
)
"""Which route answers for each shape that is a picture.

A table rather than a conditional, so that adding a shape is an entry here and
the two shapes that are *not* pictures need no mention at all: a missing key is
"no URL", which is exactly what an excerpt and a symbol want.
"""

LAYOUT_PER_PAGE: Mapping[LibraryLayout, int] = MappingProxyType(
    {LibraryLayout.GRID: 60, LibraryLayout.LIST: DEFAULT_PER_PAGE}
)
"""How many entries each layout shows at once.

More in the grid, because a tile is read at a glance and a row is read. Sixty is
also what the measurement in the sprint's plan is written against: it is the
number that decides how much a page transfers.
"""


def library_view(
    page: LibraryPage,
    previews: tuple[Preview, ...] | None = None,
    *,
    layout: LibraryLayout = LibraryLayout.GRID,
) -> dict[str, Any]:
    """Return what the library page shows, links included.

    The sort links and the paging links are built here rather than in the
    template, because each of them is *this* query with one thing changed — and
    a template assembling query strings is a template deciding something. The
    layout rides along the same way, so that no link on the page can drop it.

    *previews* comes from the service and is parallel to ``page.items``. Absent,
    every tile falls back to its symbol, which is what a caller with no interest
    in tiles wants and costs it nothing to ask for.
    """
    query = page.query
    shown = previews if previews is not None else ((None,) * len(page.items))
    return {
        "rows": tuple(
            _library_row(item, preview, back=_library_url(query, layout))
            for item, preview in zip(page.items, shown, strict=True)
        ),
        "columns": tuple(_column(label, sort, query, layout) for label, sort in COLUMNS),
        "layout": str(layout),
        "is_grid": layout is LibraryLayout.GRID,
        # Both, always, and the one you are on marked rather than hidden: a
        # control that disappears when it is active is one you cannot find your
        # way back from.
        "layouts": tuple(
            {
                "label": LAYOUT_WORDS[option],
                "url": _library_url(query, option, page=1),
                "active": option is layout,
            }
            for option in LibraryLayout
        ),
        "total": format_number(page.total),
        "stored": format_number(page.stored),
        "shown": f"{format_number(page.first)}–{format_number(page.last)}",
        "has_rows": bool(page.items),
        "is_filtered": query.is_filtered,
        "search": query.search,
        "provider": query.provider or "",
        "status": "" if query.status is None else str(query.status),
        # Carried through the filter form as hidden fields, so searching keeps
        # the order you had chosen instead of silently resetting it.
        "sort_value": str(query.sort),
        "direction": "desc" if query.descending else "asc",
        "kind": "" if query.kind is None else str(query.kind),
        "state": "queued" if query.queued else "",
        "verdict": _enum_value(query.verdict),
        "fav": "1" if query.favourite else "",
        # Where the batch of ticked entries is posted, and where it comes back
        # to: the listing exactly as it is now, so a judgement lands you where
        # you were rather than at the top of an unfiltered library.
        "review_action": _review_action("/library/review", _library_url(query, layout)),
        "verdict_buttons": VERDICT_CHOICES,
        # Shown in the two boxes, and rounded to what `format_size` prints: a
        # bound is a coarse instrument, and "1.2 MB" beside a listing that says
        # "1.3 MB" everywhere else would be the odd one out. What travels in the
        # URL is the byte count, so a bookmark means one thing.
        "min_size": format_size(query.min_size) if query.min_size is not None else "",
        "max_size": format_size(query.max_size) if query.max_size is not None else "",
        "facets": _library_facets(page, layout),
        "page": format_number(page.page),
        "pages": format_number(page.pages),
        "previous_url": (
            _library_url(query, layout, page=page.page - 1) if page.has_previous else None
        ),
        "next_url": _library_url(query, layout, page=page.page + 1) if page.has_next else None,
        "reset_url": _library_url(LibraryQuery(), layout),
    }


def item_view(
    item: LibraryItem,
    payload: StoredPayload | None,
    *,
    back: str = "/library",
    place: LibraryPlace | None = None,
) -> dict[str, Any]:
    """Return what one stored file's page shows.

    *payload* is what the service found on disk, and is ``None`` for two very
    different situations that the page has to tell apart: a download that failed
    and never wrote a file, and a record claiming a file that has since been
    deleted or moved. The first is a reason; the second is a repair.

    *place* turns the page from *a file* into *the twelfth of forty*. Given one,
    the page grows a position, a link either way, and buttons that move on when
    they are pressed; without one it is what it has always been. The page is the
    same page either way, which is what keeps a bookmarked file and a file being
    worked through from becoming two designs.
    """
    base = f"/library/{item.directory}/{item.key}"
    return {
        "walk": _walk_view(place, back),
        "name": item.name,
        "provider": item.provider,
        "directory": item.directory,
        "key": item.key,
        "status": str(item.status),
        "state_label": _entry_label(item),
        "state_tone": _entry_tone(item),
        "is_queued": item.queued,
        "size": format_size(item.size),
        "filename": item.filename,
        "downloaded_at": (
            None if item.downloaded_at is None else format_timestamp(item.downloaded_at)
        ),
        "discovered_at": (
            None if item.discovered_at is None else format_timestamp(item.discovered_at)
        ),
        "source_url": item.source_url,
        "path": None if item.path is None else str(item.path),
        "checksum": item.checksum,
        "attempts": format_number(item.attempts),
        "error": item.error,
        "library_url": back,
        "file_url": f"{base}/file" if payload is not None else None,
        "is_stored": payload is not None,
        # How, and whether, the page embeds the file itself. `display` is the
        # element to use rather than a type to branch on, so the template asks
        # no questions about media at all.
        "view_url": f"{base}/view" if _can_display(payload) else None,
        "display": None if payload is None else str(payload.media.display),
        "view_reason": None if payload is None else payload.media.reason,
        # Whether the frame showing it needs the `sandbox` attribute. True for
        # the types that could execute script; false for a PDF, which Chrome
        # refuses to render inside a sandboxed frame at all, and which cannot
        # reach our origin anyway.
        "sandboxed": payload is not None and payload.media.is_script_capable,
        # The record says there is a file and there is not: worth its own
        # sentence, because the answer is to download it again rather than to
        # wonder what the page means.
        #
        # Not said about something discarded, where the file being gone is the
        # point rather than a fault. Both sentences end in "download it again",
        # and telling somebody their own decision was an accident is the kind of
        # small wrongness that makes a page feel like it is not paying attention.
        "payload_missing": (
            payload is None and item.is_stored and item.verdict is not ReviewVerdict.DISCARDED
        ),
        # Judging from here lands back *here* when the page was opened on its
        # own: somebody standing on one file is looking at it, and being thrown
        # back to a table would be the interface deciding they were finished.
        # Reached from a listing, a decision moves on to the next file in it
        # instead — which is the same rule read from the other side, because
        # there the next file is what they are standing in front of.
        **_review_of(
            item,
            base,
            item_url(item.directory, item.key, back),
            walk=None if place is None else back,
        ),
    }


def item_url(directory: str, key: str, back: str) -> str:
    """Return one file's page, carrying the listing it is being walked from.

    The ``back`` is what makes it a walk rather than a visit: a page that was
    reached from a listing knows which one, and therefore knows what comes next.
    Always written, including for the unfiltered library — the parameter used to
    be dropped there to keep the URL short, and it stopped being decoration the
    moment something read it.
    """
    return f"/library/{directory}/{key}?{urlencode({'back': back})}"


def _walk_view(place: LibraryPlace | None, back: str) -> dict[str, Any] | None:
    """Return the header that says where in a listing this file stands.

    ``None`` when there is no listing being walked, which the template reads as
    *show none of this*. The neighbours carry the same ``back``, so moving on
    keeps the walk rather than ending it on the second file.
    """
    if place is None:
        return None
    previous = place.previous
    following = place.following
    return {
        "position": format_number(place.position),
        "total": format_number(place.total),
        "previous_url": None
        if previous is None
        else item_url(previous.directory, previous.key, back),
        "previous_name": None if previous is None else previous.name,
        "next_url": None
        if following is None
        else item_url(following.directory, following.key, back),
        "next_name": None if following is None else following.name,
    }


def discard_view(items: Iterable[LibraryItem], *, action: str, back: str) -> dict[str, Any]:
    """Return what the page asking about a batch of deletions shows.

    The one confirmation in the interface, and it exists where the damage scales:
    a tile is one file somebody is looking at, and a selection is two hundred
    they cannot all see. What it has to answer before anybody presses anything is
    *how many* and *which* — a count alone is a number to agree with rather than
    a list to check.

    The freed size counts only what still has a payload. A selection that
    happens to include something already discarded frees nothing further for it,
    and saying otherwise would inflate the one number the page exists to state.
    """
    rows = tuple(items)
    return {
        "rows": tuple(
            {
                "token": f"{item.directory}/{item.key}",
                "name": item.name,
                "size": format_size(item.size),
                "url": f"/library/{item.directory}/{item.key}",
                "is_discarded": item.verdict is ReviewVerdict.DISCARDED,
            }
            for item in rows
        ),
        "count": format_number(len(rows)),
        "is_one": len(rows) == 1,
        "freed": format_size(
            sum(
                item.size or 0
                for item in rows
                if item.verdict is not ReviewVerdict.DISCARDED and item.is_stored
            )
        ),
        "action": action,
        # Where cancelling goes, and it is the listing the ticks were made in:
        # a confirmation that dumps somebody at the top of an unfiltered library
        # has cost them the selection and the filter for saying no.
        "back": back,
    }


SIZE_RANGES: tuple[tuple[str, int | None, int | None], ...] = (
    ("under 1 MB", None, 1_000_000),
    ("1–10 MB", 1_000_000, 10_000_000),
    ("10–100 MB", 10_000_000, 100_000_000),
    ("over 100 MB", 100_000_000, None),
)
"""The size bands offered as one click, and a partition of every stated size.

Contiguous and non-overlapping on purpose: a row belongs to exactly one of them,
so the four counts add up to the number of entries whose size is known. The
boundaries are inclusive at the top, which is why "1–10 MB" starts where "under
1 MB" stops rather than one byte later — a file of exactly a megabyte is in the
lower band, and being in both would break the same property.
"""


def _library_facets(page: LibraryPage, layout: LibraryLayout) -> tuple[dict[str, Any], ...]:
    """Return the chip rows that narrow a listing in one click.

    A chip you are standing on links back to the listing without it, so every
    chip is a toggle rather than a one-way door — the same behaviour the report's
    chips have, built the same way.

    A group with one chip in it is dropped. "Show me the only kind of thing here"
    is a control whose two states show the same rows.
    """
    query = page.query
    rows = (
        # First, because it is the row somebody sorting through a crawl works
        # from: which of these have I not looked at yet.
        _facet_row(
            "Review",
            page.verdicts,
            query,
            layout,
            "verdict",
            _enum_value(query.verdict),
            lambda value: VERDICT_WORDS[ReviewVerdict(value)],
            extra=_favourite_chips(page, layout),
        ),
        _facet_row("Source", page.providers, query, layout, "provider", query.provider or "", str),
        _facet_row(
            "Type",
            page.kinds,
            query,
            layout,
            "kind",
            "" if query.kind is None else str(query.kind),
            lambda value: KIND_WORDS[MediaKind(value)],
        ),
        _facet_row(
            "State",
            page.statuses,
            query,
            layout,
            "status",
            _status_value(query.status),
            lambda value: STATUS_LABELS[DownloadStatus(value)],
            extra=_queued_chips(page, layout),
        ),
        _size_facets(query, layout),
    )
    return tuple(row for row in rows if row is not None)


def _facet_row(
    heading: str,
    facets: tuple[LibraryFacet, ...],
    query: LibraryQuery,
    layout: LibraryLayout,
    name: str,
    active: str,
    label_of: Callable[[str], str],
    *,
    extra: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any] | None:
    """Return one chip row, or ``None`` when it would not be worth showing.

    *extra* is for a chip that belongs in a group without coming from its
    facets, and the length is checked after they are added — a library where
    everything completed and one thing is being fetched again has two states to
    choose between, even though only one of them is a status.
    """
    chips = [
        _chip(
            query,
            layout,
            label_of(facet.value),
            facet.count,
            active=facet.value == active,
            **{name: None if facet.value == active else facet.value},
        )
        for facet in facets
    ]
    chips.extend(extra)
    return None if len(chips) < 2 else {"heading": heading, "chips": tuple(chips)}


def _favourite_chips(page: LibraryPage, layout: LibraryLayout) -> tuple[dict[str, Any], ...]:
    """Return the "starred" chip, when anything is starred or it is the filter on.

    Beside the verdicts although it is not one of them, for the reason the queue
    chip sits beside the download states: the group is what somebody looks
    through to narrow a listing, and which of its chips come from the same
    vocabulary is this module's problem rather than theirs.
    """
    query = page.query
    if page.favourites == 0 and not query.favourite:
        return ()
    return (
        _chip(
            query,
            layout,
            FAVOURITE_LABEL,
            page.favourites,
            active=query.favourite,
            favourite=not query.favourite,
        ),
    )


def _queued_chips(page: LibraryPage, layout: LibraryLayout) -> tuple[dict[str, Any], ...]:
    """Return the "waiting" chip, when there is a queue to ask and it holds something.

    Offered beside the download states because that is where somebody looks for
    it, and built separately because it is not one: a status is what a record
    says happened, and this is what is happening right now in this process.

    Nothing at all when no queue was handed to the service, which is what tells
    a page that cannot answer the question apart from one whose answer is none.
    Kept on the page while it is the active filter even at zero, so the chip you
    are standing on is always the one that switches it off.
    """
    query = page.query
    if page.queued is None or (page.queued == 0 and not query.queued):
        return ()
    return (
        _chip(
            page.query,
            layout,
            QUEUED_LABEL,
            page.queued,
            active=query.queued,
            queued=not query.queued,
        ),
    )


def _chip(
    query: LibraryQuery,
    layout: LibraryLayout,
    label: str,
    count: int | None,
    *,
    active: bool,
    **changes: Any,
) -> dict[str, Any]:
    """Return one chip: what it says, whether it is on, and where it leads.

    Always back to the first page, because a filter applied on page seven of the
    old listing has no page seven to keep.
    """
    return {
        "label": label,
        "count": "" if count is None else format_number(count),
        "active": active,
        "url": _library_url(query, layout, page=1, **changes),
    }


def _size_facets(query: LibraryQuery, layout: LibraryLayout) -> dict[str, Any]:
    """Return the size bands as chips, with the active one able to switch off.

    No counts, and that is the one place these differ from the chips above.
    A band's count would have to be computed over the library while the bands
    themselves are what somebody is choosing between, and the number that
    matters — how many the chosen band holds — is already the one at the top of
    the page.
    """
    return {
        "heading": "Size",
        "chips": tuple(
            _chip(
                query,
                layout,
                label,
                None,
                active=query.min_size == low and query.max_size == high,
                min_size=None if query.min_size == low and query.max_size == high else low,
                max_size=None if query.min_size == low and query.max_size == high else high,
            )
            for label, low, high in SIZE_RANGES
        ),
    }


COLUMNS: tuple[tuple[str, LibrarySort], ...] = (
    ("Provider", LibrarySort.PROVIDER),
    ("Name", LibrarySort.NAME),
    ("Size", LibrarySort.SIZE),
    ("Downloaded", LibrarySort.DOWNLOADED),
    ("Status", LibrarySort.STATUS),
)
"""The sortable columns, in the order they are read."""

SORT_MARKS = {True: "▾", False: "▴"}
"""What marks the column a listing is ordered by, and which way."""


def _column(
    label: str, sort: LibrarySort, query: LibraryQuery, layout: LibraryLayout
) -> dict[str, Any]:
    """Return one column heading, and the link that reorders by it.

    Clicking the active column reverses it; clicking another one starts at the
    direction that column is most often wanted in — largest file and newest
    download first, names from A. Guessing right saves a second click and
    guessing wrong costs one, so the guess is worth making.
    """
    active = query.sort is sort
    descending = not query.descending if active else sort in _DESCENDING_FIRST
    return {
        "label": label,
        "url": _library_url(query, layout, sort=sort, descending=descending, page=1),
        "active": active,
        "mark": SORT_MARKS[query.descending] if active else "",
    }


_DESCENDING_FIRST = frozenset({LibrarySort.SIZE, LibrarySort.DOWNLOADED})
"""Columns whose first click means "biggest first" rather than "smallest"."""


def _library_url(query: LibraryQuery, layout: LibraryLayout, **changes: Any) -> str:
    """Return the library URL for *query* with *changes* applied.

    Only what differs from the default is written into the query string, so an
    unfiltered listing is plain ``/library`` and a bookmarked one carries exactly
    what it needs. The layout follows the same rule and is therefore absent from
    every grid link: the grid is what ``/library`` means.
    """
    values = {
        "q": changes.get("search", query.search),
        "view": "" if layout is LibraryLayout.GRID else str(layout),
        "provider": changes.get("provider", query.provider) or "",
        "status": _status_value(changes.get("status", query.status)),
        "kind": _enum_value(changes.get("kind", query.kind)),
        "min": _number_value(changes.get("min_size", query.min_size)),
        "max": _number_value(changes.get("max_size", query.max_size)),
        "verdict": _enum_value(changes.get("verdict", query.verdict)),
        "fav": "1" if changes.get("favourite", query.favourite) else "",
        "state": "queued" if changes.get("queued", query.queued) else "",
        "sort": str(changes.get("sort", query.sort)),
        "dir": "desc" if changes.get("descending", query.descending) else "asc",
        "page": str(changes.get("page", query.page)),
    }
    default = LibraryQuery()
    if values["sort"] == str(default.sort) and values["dir"] == "desc":
        del values["sort"], values["dir"]
    if values.get("page") == "1":
        del values["page"]
    written = {name: value for name, value in values.items() if value}
    return f"/library?{urlencode(written)}" if written else "/library"


def _status_value(status: DownloadStatus | None) -> str:
    """Return a status as a query string writes it, or nothing."""
    return "" if status is None else str(status)


def _enum_value(value: StrEnum | None) -> str:
    """Return an optional enumerated filter as a query string writes it."""
    return "" if value is None else str(value)


def _number_value(value: int | None) -> str:
    """Return an optional numeric bound as a query string writes it.

    Written as a plain byte count rather than as "10 MB", so a bookmarked URL
    means exactly one number and does not depend on this module's rounding.
    """
    return "" if value is None else str(value)


def _library_row(
    item: LibraryItem, preview: Preview | None = None, *, back: str = "/library"
) -> dict[str, Any]:
    """Return one stored resource as a row, and as a tile.

    One dictionary for both layouts rather than two, because they show the same
    entry: what differs is which template reads which keys. Two builders would
    make it possible for a tile and a row to disagree about the same file, which
    is exactly the bug nobody would look for.
    """
    base = f"/library/{item.directory}/{item.key}"
    shape = PreviewShape.SYMBOL if preview is None else preview.shape
    return {
        "provider": item.provider,
        "name": item.name,
        # Elided here rather than by the stylesheet: CSS can only cut the end
        # off, which is where the extension is. See `elide_middle`.
        "short_name": elide_middle(item.name, TILE_NAME_LENGTH),
        "size": format_size(item.size),
        # What the tile puts where the file would be, and which route answers
        # for it. A thumbnail comes from its own route because it is a file this
        # application made; the stored image comes from the route that decides
        # what a browser may be shown, which is a different question and stays
        # one.
        "preview_shape": str(shape),
        "preview_url": PREVIEW_ROUTES.get(shape, lambda _: None)(base),
        "excerpt": "" if preview is None else preview.excerpt,
        "is_queued": item.queued,
        "downloaded_at": (
            "—" if item.downloaded_at is None else format_timestamp(item.downloaded_at)
        ),
        # Native separators, unlike the configured paths on the settings page.
        # A configured value is written into a TOML file, which spells them one
        # way on every platform; this is a location somebody pastes into their
        # file manager, and there it has to be spelled the way the platform does.
        "path": "—" if item.path is None else str(item.path),
        "source_url": item.source_url,
        "status": str(item.status),
        "state_label": _entry_label(item),
        "state_tone": _entry_tone(item),
        "kind": str(item.kind),
        "kind_word": KIND_WORDS[item.kind],
        # Carrying the listing, which is what makes opening a tile the start of
        # a walk through it rather than a visit to one file.
        "url": item_url(item.directory, item.key, back),
        **_review_of(item, base, back),
    }


def _review_of(
    item: LibraryItem, base: str, back: str, *, walk: str | None = None
) -> dict[str, Any]:
    """Return what a row says and offers about the judgement passed on it.

    The same keys on a tile, on a table row and on the file's own page, because
    the four buttons are the same four buttons everywhere — which is most of
    what makes judging a hundred files feel like one action repeated rather than
    three interfaces.
    """
    discarded = item.verdict is ReviewVerdict.DISCARDED
    return {
        "verdict": str(item.verdict),
        "verdict_word": VERDICT_WORDS[item.verdict],
        "is_reviewed": item.verdict is not ReviewVerdict.UNREVIEWED,
        "is_discarded": discarded,
        # The one undo that cannot undo everything, and it says so where it is
        # pressed rather than afterwards: the verdict goes, the file does not
        # come back, and fetching the link again is what would restore it.
        "undo_hint": (
            "Put this back in the unreviewed pile — the deleted file does not come back"
            if discarded
            else "Put this back in the unreviewed pile"
        ),
        "is_favourite": item.favourite,
        # Filled or hollow, and the value it would send is the opposite of what
        # it shows: the button says what is true now and does the other thing.
        "favourite_mark": FAVOURITE_MARK if item.favourite else UNFAVOURITE_MARK,
        "favourite_value": "0" if item.favourite else "1",
        "entry_token": f"{item.directory}/{item.key}",
        "review_action": _review_action(f"{base}/review", back, walk=walk),
        # Carried on the row rather than beside it, so the partial that renders
        # the buttons reads the same name whether it is inside a tile, a table
        # row or one file's own page.
        "verdict_buttons": VERDICT_CHOICES,
    }


def _review_action(path: str, back: str, *, walk: str | None = None) -> str:
    """Return where a judgement is posted, carrying where to land afterwards.

    On the action rather than in a hidden field, which is the arrangement
    ADR-039 settled on: the same button sits on a listing, on a tile and on a
    file's own page, and each of those is a different place to come back to.
    :func:`~maxicrawler.api.routes._our_path` is what makes the parameter safe
    to obey.

    *walk* names the listing being worked through, and its presence is what
    makes a decision move on to the next file of it. Its own parameter rather
    than a flag on ``back``, because the two say different things: ``back`` is
    where a press that does *not* move on should land — the file itself, as it
    has been since these buttons existed — and this is the set the next file
    comes out of. A file opened on its own has the first and not the second.
    """
    asked = {"back": back} if walk is None else {"back": back, "walk": walk}
    return f"{path}?{urlencode(asked)}"


def _entry_label(item: LibraryItem) -> str:
    """Return what a listing says about this entry's state.

    The queue wins over the record, because it is the newer of the two facts. A
    row reading "failed" while the file is being fetched again would send
    somebody to press a button that is already pressed.
    """
    return QUEUED_LABEL if item.queued else STATUS_LABELS[item.status]


def _entry_tone(item: LibraryItem) -> str:
    """Return the badge colour that goes with :func:`_entry_label`."""
    return "idle" if item.queued else STATUS_TONES[item.status]


def _transferred(written: int, total: int | None) -> str:
    """Return the byte counter under the bar, in the one form that reads well.

    "1.3 MB of 2.8 MB" while a total is known, and just what has arrived while
    it is not — never "1.3 MB of unknown", which is a sentence nobody wants.
    """
    if total is None:
        return format_size(written)
    return f"{format_size(written)} of {format_size(total)}"


def report_view(report: CrawlReport) -> dict[str, Any]:
    """Return what a finished crawl's page shows."""
    statistics = report.statistics
    summary = report.summary
    return {
        "job_id": report.session.session_id,
        "seed_url": report.seed_url,
        "state": str(report.state),
        "state_label": STATE_LABELS[report.state],
        "state_tone": STATE_TONES[report.state],
        "options": describe_options(report.session.options),
        "elapsed": format_duration(statistics.elapsed_seconds),
        "finished_at": format_timestamp(report.finished_at),
        "pages_visited": format_number(statistics.pages_visited),
        "pages_failed": format_number(statistics.pages_failed),
        "pages_attempted": format_number(statistics.pages_attempted),
        "pages_skipped": format_number(statistics.pages_skipped),
        "max_depth_reached": statistics.max_depth_reached,
        "frontier_remaining": format_number(statistics.frontier_remaining),
        "requests_without_a_page": format_number(statistics.requests_without_a_page),
        "links_found": format_number(summary.total_urls),
        "unique_urls": format_number(summary.unique_urls),
        "duplicates_removed": format_number(summary.duplicates_removed),
        "unresolved_urls": format_number(summary.statistics.unresolved_urls),
        "has_failures": statistics.pages_failed > 0,
        "hit_the_page_limit": report.state is CrawlState.PAGE_LIMIT,
        "left_in_frontier": statistics.frontier_remaining > 0,
        # Worth a line only when it differs from the two visible counters: it
        # is what explains a page ceiling arriving sooner than they suggest.
        # A boolean, so no template ends up comparing a formatted number.
        "had_answers_that_were_not_pages": statistics.requests_without_a_page > 0,
        "skips": _skip_rows(statistics.skips_by_reason),
        "link_kinds": _kind_rows(statistics.links_by_kind),
        "plugins": plugin_shares(summary.plugin_usage),
        "json_url": f"/crawls/{report.session.session_id}.json",
    }


PAGE_STATE_LABELS: dict[PageState, str] = {
    PageState.SUCCEEDED: "read",
    PageState.FAILED: "failed",
    PageState.REDIRECTED: "redirected",
}
"""What each state of a crawled page is called on a page.

"read" rather than "succeeded", because the counter above the table has said
"Pages read" since the first version of this report and two words for one
number is how a reader starts wondering whether they are two numbers.
"""

PAGE_PARAMS = frozenset({"pq", "pstate", "ppage"})
"""Which query parameters the page table owns.

Both tables live on one URL, so each has to know which parameters are not its
own — and carry them through untouched. Without that, filtering the pages would
silently throw away the link filter you were looking at, which is the kind of
thing that teaches somebody to stop using the filters.
"""

SHUT_PARAM = "shut"
"""Which query parameter names the collapsed panels; see :func:`panel_view`."""

PANELS: tuple[str, ...] = ("summary", "pages", "links")
"""The parts of a report that can be folded away, in the order they appear.

Named here rather than discovered from the templates, because the value in the
query string is one of these words and a report has to be able to ignore a word
that is not.
"""


def panel_view(
    shut: Container[str], *, base: str, carry: Mapping[str, str] = MappingProxyType({})
) -> dict[str, dict[str, Any]]:
    """Return, for each panel, whether it is open and the link that flips it.

    A link and a query parameter rather than a ``<details>`` element, which is
    what the three breakdowns inside the summary still are — and the difference
    is what this exists for. A ``<details>`` forgets on every click, which is
    right for a breakdown you open to read once and wrong for a table you are
    keeping out of your way while you work the one below it. Filtering, sorting,
    paging and choosing columns all already survive a click by living in the
    URL; this was the only part of *how you have the report set up* that did not.

    The cost is a page load to fold something, and it is the right way round:
    you pay it once and every reload afterwards is the shorter page.

    Each link carries the rest of the query string untouched, so folding the
    page table cannot disturb the link filter beside it, and lands on the panel
    it just changed rather than at the top — a control that scrolls away from
    itself is one nobody uses twice.
    """
    closed = frozenset(name for name in PANELS if name in shut)
    return {
        name: {
            "is_open": name not in closed,
            "label": "Expand" if name in closed else "Collapse",
            "url": _panel_url(base, carry, closed ^ {name}, at=name),
        }
        for name in PANELS
    }


def _panel_url(base: str, carry: Mapping[str, str], closed: Container[str], *, at: str) -> str:
    """Return the report URL with *closed* the set of folded panels."""
    written = dict(carry)
    value = ",".join(name for name in PANELS if name in closed)
    if value:
        written[SHUT_PARAM] = value
    return f"{base}?{urlencode(written)}#{at}" if written else f"{base}#{at}"


TRANSIENT_PARAMS = frozenset({"queued", "bad", "full", "held"})
"""What a report says once and then stops saying.

The outcome of the batch that sent you back here. Neither table owns these and
*both* have to drop them: a confirmation is about the click that just happened,
and carrying it forward would make it a claim about every click after — you
would change the filter and still be told twelve links were queued.

Dropping them is all it takes to make the strip disappear on the next click,
which is why nothing anywhere has to remember having shown it.
"""


def page_view(
    slice: PageSlice, *, base: str, carry: Mapping[str, str] = MappingProxyType({})
) -> dict[str, Any]:
    """Return the page table, its filter, its chips and its paging.

    The same shape as :func:`link_view` and for the same reasons; what differs
    is that these records were never written down, so there is no order to
    choose and no column to hide. The order a crawl reached its pages in is the
    only one that means anything.
    """
    query = slice.query
    return {
        "rows": page_rows(slice.items),
        "total": format_number(slice.total),
        "recorded": format_number(slice.recorded),
        "has_rows": bool(slice.items),
        "has_any": slice.recorded > 0,
        "is_filtered": query.is_filtered,
        "action": f"{base}#pages",
        "search": query.search,
        "state": "" if query.state is None else str(query.state),
        "chips": tuple(
            {
                "label": PAGE_STATE_LABELS[state],
                "count": format_number(slice.counts.of(state)),
                "active": query.state is state,
                "tone": "bad" if state is PageState.FAILED else "",
                "url": _page_url(
                    base,
                    query,
                    carry,
                    state=None if query.state is state else state,
                    page=1,
                ),
            }
            for state in PageState
            if slice.counts.of(state)
        ),
        "carried": tuple({"name": name, "value": value} for name, value in sorted(carry.items())),
        "page": format_number(slice.page),
        "pages": format_number(slice.pages),
        "shown_range": f"{format_number(slice.first)}–{format_number(slice.last)}",
        "previous_url": (
            _page_url(base, query, carry, page=slice.page - 1) if slice.has_previous else None
        ),
        "next_url": _page_url(base, query, carry, page=slice.page + 1) if slice.has_next else None,
        "reset_url": _page_url(base, PageQuery(), carry),
    }


def _page_url(base: str, query: PageQuery, carry: Mapping[str, str], **changes: Any) -> str:
    """Return the report URL for the page table's *query* with *changes* applied.

    *carry* is every parameter this table does not own, written back unchanged.
    """
    state = changes.get("state", query.state)
    values = {
        "pq": changes.get("search", query.search),
        "pstate": "" if state is None else str(state),
        "ppage": str(changes.get("page", query.page)),
    }
    if values["ppage"] == "1":
        del values["ppage"]
    written = {**carry, **{name: value for name, value in values.items() if value}}
    return f"{base}?{urlencode(written)}#pages" if written else f"{base}#pages"


@dataclass(frozen=True, slots=True)
class LinkColumn:
    """One column of the link table, and whether it can be ordered by."""

    name: str
    label: str
    sort: LinkSort | None = None


LINK_COLUMNS: tuple[LinkColumn, ...] = (
    LinkColumn("state", "State"),
    LinkColumn("plugin", "Plugin", LinkSort.PLUGIN),
    LinkColumn("category", "Category"),
    LinkColumn("target", "Type"),
    LinkColumn("url", "URL", LinkSort.URL),
    LinkColumn("raw", "As written"),
    LinkColumn("source", "Found on", LinkSort.SOURCE),
)
"""The columns a reader can turn off, in the order they are read.

``state`` leads, next to the checkboxes, because it is the column a person is
reading *in order to* tick a box. Everything else in the row describes what the
URL is; this one is the only thing that says whether you already have it.

``raw`` is a column rather than the second line under the URL it used to be. On
a crawl where most URLs were rewritten that line doubled the height of the whole
table, and a reader who does not care what a link said before normalisation had
no way to say so. As a column it is one line either way and turns off like the
rest — which is the point: the same rows, on half the screen.

``state``, ``raw``, ``category`` and ``target`` are not sortable, and
deliberately have no ordering of their own. The first three are short labels
with a handful of values, and grouping by them is what the facet chips already
do in one click; the last would order almost exactly as ``url`` does, and a
second heading that sorts the same way is a heading that teaches nothing.
"""

STATE_COLUMN = "state"
"""The one column that is not always there; see :func:`link_view`."""

LINK_STATE_LABELS: dict[str, str] = {
    UNTRACKED: "new",
    LinkState.IN_LIBRARY: "in library",
    LinkState.IN_QUEUE: "in queue",
    LinkState.DISMISSED: "dismissed",
}
"""What each state is called, keyed the way a query string spells it.

Nouns rather than sentences. *"In library"* is true of a folder share the moment
one file inside it is stored; *"downloaded"* would not be, and a wording that
becomes a lie as soon as containers exist is not a wording to build a filter on.
"""

LINK_STATE_TONES: dict[str, str] = {
    UNTRACKED: "idle",
    LinkState.IN_LIBRARY: "good",
    LinkState.IN_QUEUE: "busy",
    # Quiet like "new". It marks a row nobody has to act on, and a colour that
    # asked for attention would be asking for it on behalf of a decision that
    # has already been made.
    LinkState.DISMISSED: "idle",
}
"""Which badge colour each state wears. "New" is the quiet one deliberately: on
a first crawl it is every row, and a table shouting at all three thousand of
them draws the eye to nothing."""

REQUIRED_COLUMN = "url"
"""The one column that cannot be hidden.

A table of discovered URLs without the URLs is not a narrower view of anything.
"""

LINK_ORDERS: tuple[tuple[LinkSort, str], ...] = (
    (LinkSort.RELEVANCE, "Plugin relevance"),
    (LinkSort.DISCOVERED, "Discovery order"),
    (LinkSort.URL, "URL"),
    (LinkSort.PLUGIN, "Plugin name"),
    (LinkSort.SOURCE, "Page it was found on"),
)
"""Every order offered, including the two that are not columns.

"Plugin relevance" is the default and is named rather than left implicit: a
reader who notices that Mega links are on top deserves to be told that this is
a choice, and given the one that is not.
"""

TARGET_LABELS: dict[TargetKind, str] = {
    TargetKind.DOCUMENT: "documents",
    TargetKind.IMAGE: "images",
    TargetKind.ARCHIVE: "archives",
    TargetKind.VIDEO: "video",
    TargetKind.AUDIO: "audio",
    TargetKind.PAGE: "pages (.html)",
    TargetKind.UNKNOWN: "not stated",
}
"""What each target kind is called on a page.

"pages (.html)" says the extension because the filter means exactly that and
nothing wider, and "not stated" rather than "unknown" because the URL not
saying is a fact about the URL, not a gap in what we know.
"""

DOWNLOADABLE_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "any"),
    ("yes", "can be downloaded"),
    ("no", "cannot"),
)


LINK_PARAMS = frozenset(
    {"q", "plugin", "category", "target", "state", "dl", "norm", "sort", "dir", "page", "hide"}
)
"""Which query parameters the link table owns; see :data:`PAGE_PARAMS`."""


@dataclass(frozen=True, slots=True)
class QueuedBatch:
    """What a batch of links did to the queue, on the way back to the report."""

    queued: int
    rejected: int = 0
    """How many were not links this installation could have fetched."""

    no_room: int = 0
    """How many matched or were selected and did not fit."""

    held: int = 0
    """How many were already in the queue and were not queued a second time."""


def _queued_notice(batch: QueuedBatch) -> dict[str, Any]:
    """Return the strip a report shows about the batch that just left it.

    Three numbers rather than one, because the interesting outcome is the
    partial one: a selection of two hundred where the queue took a hundred and
    fifty is a job mostly done, and a page saying only *"150 links queued"*
    leaves somebody to discover the other fifty by counting.

    The remainder is not restated as an instruction. What to do about a full
    queue is on the queue's own page, which the strip links to, and a report
    that started giving advice about a queue would be the second place to
    maintain the same sentence.
    """
    notes = []
    if batch.held:
        # First of the three, because it is the one that is not a problem:
        # pressing this twice on a filter that has half drained is ordinary,
        # and the answer is that nothing was lost.
        notes.append(f"{format_number(batch.held)} were already in the queue.")
    if batch.no_room:
        notes.append(f"{format_number(batch.no_room)} did not fit — the queue is full.")
    if batch.rejected:
        notes.append(
            f"{format_number(batch.rejected)} could not be fetched by the providers installed here."
        )
    return {"sentence": _queued_sentence(batch.queued), "notes": tuple(notes)}


def _queued_sentence(queued: int) -> str:
    """Return what the strip leads with, counted rather than pluralised badly."""
    if queued == 0:
        return "Nothing was queued."
    if queued == 1:
        return "1 link queued."
    return f"{format_number(queued)} links queued."


def link_view(
    page: LinkPage,
    *,
    base: str,
    hidden: Container[str] = (),
    carry: Mapping[str, str] = MappingProxyType({}),
    downloads_everything: bool = False,
    queued: QueuedBatch | None = None,
) -> dict[str, Any]:
    """Return the link table, its filters, its facets and its paging.

    Everything that decides *which* URLs these are was decided by
    :class:`~maxicrawler.app.discovery.DiscoveryService` before this was called.
    What is left is wording, formatting, and building the links — each of which
    is this query with one thing changed, which is exactly the kind of decision
    a template must not be making.

    *base* is the page the table lives on, because a report's URL contains the
    crawl it belongs to. Every link this builds ends at ``#links``, so choosing
    a filter puts you back at the table rather than at the top of a long page.

    *downloads_everything* withdraws the download filter. Where every recorded
    link can be fetched, that control has one populated bucket and one empty
    one, and offering *"show me the ones that cannot"* is offering an empty
    table. The facets beside it already work this way — a plugin nothing used
    is not listed — so a filter that separates nothing is not shown either.
    The parameter, rather than a guess from the rows on screen: one page of a
    crawl is not evidence about the crawl, and it is the installation that
    decides this, not the links.

    The state column is withdrawn the same way, but on evidence the page
    carries: :attr:`~maxicrawler.app.LinkPage.known` is empty exactly when
    nothing was asked, and a column of badges reading "new" against a question
    nobody put would be a claim rather than an answer.

    *queued* is what a batch did on its way back here, and is absent on every
    other view of the report. It has no effect on the links this builds, which
    is what makes the confirmation last exactly one page: nothing carries it
    forward, so nothing has to remember having shown it.
    """
    query = page.query
    rows = link_rows(page)
    columns = tuple(column for column in LINK_COLUMNS if column.name != STATE_COLUMN or page.known)
    shown = frozenset(column.name for column in columns if column.name not in hidden) | frozenset(
        {REQUIRED_COLUMN}
    )
    return {
        "rows": rows,
        "shown": shown,
        "headers": tuple(
            _link_header(column, query, base=base, hidden=hidden, carry=carry)
            for column in columns
            if column.name in shown
        ),
        "toggles": tuple(
            _link_toggle(column, query, base=base, hidden=hidden, carry=carry) for column in columns
        ),
        "total": format_number(page.total),
        "recorded": format_number(page.recorded),
        "discovered": format_number(page.discovered),
        "was_recorded": page.was_recorded,
        "has_rows": bool(page.items),
        # Whether there is anything to filter at all, which is a different
        # question from whether this page has rows: a filter bar above "nothing
        # matches that" is useful, and one above a crawl that found nothing is
        # a control with no purpose.
        "has_any": page.recorded > 0,
        # A column of empty cells is worse than no column: the table only grows
        # an action when at least one row actually has one.
        "has_downloads": any(row["can_download"] for row in rows),
        "is_filtered": query.is_filtered,
        "action": f"{base}#links",
        # Where a batch of ticked rows goes. One form for the whole table,
        # which the checkboxes join by id rather than by sitting inside it —
        # each row already has a form of its own, and forms cannot nest.
        "selection_action": SELECTION_ACTION,
        # Where "queue everything this matches" goes. The filter travels in the
        # query string of the action and the URLs never leave the server, which
        # is what makes this the half of the feature that cannot leak a key.
        "matches_action": _matches_action(base, query, hidden, carry),
        "match_count": format_number(page.total),
        # Where a batch sends the browser afterwards: this report, this filter,
        # this column layout, at the table rather than at the top of the page.
        # Only the selection needs telling — the filter form's action already
        # carries the query, and the server rebuilds the way back from it rather
        # than trusting a second copy that could disagree with the first.
        "return_to": _link_url(base, query, hidden, carry),
        # What the batch that sent you back here did, said once.
        "queued": None if queued is None else _queued_notice(queued),
        # What the filter form shows as its current state.
        "search": query.search,
        "plugin": query.plugin or "",
        "category": query.category or "",
        "target": "" if query.target is None else str(query.target),
        "state": query.state or "",
        "downloadable": _downloadable_value(query.downloadable),
        "normalized_only": query.normalized_only,
        "downloadable_choices": () if downloads_everything else DOWNLOADABLE_CHOICES,
        "orders": tuple(
            {"value": str(sort), "label": label, "selected": query.sort is sort}
            for sort, label in LINK_ORDERS
        ),
        # Carried through the filter form as hidden fields, so searching keeps
        # the order and the columns you had chosen instead of resetting them.
        "direction": "desc" if query.descending else "asc",
        "hide_value": _hide_value(hidden),
        "facets": _link_facets(page, base=base, hidden=hidden, carry=carry),
        "carried": tuple({"name": name, "value": value} for name, value in sorted(carry.items())),
        "page": format_number(page.page),
        "pages": format_number(page.pages),
        "shown_range": f"{format_number(page.first)}–{format_number(page.last)}",
        "previous_url": (
            _link_url(base, query, hidden, carry, page=page.page - 1) if page.has_previous else None
        ),
        "next_url": (
            _link_url(base, query, hidden, carry, page=page.page + 1) if page.has_next else None
        ),
        "reset_url": _link_url(base, LinkQuery(), hidden, carry),
    }


SELECTION_ACTION = "/downloads/selection"
"""Where the whole-table form posts. Named once so the template cannot drift."""


def _matches_action(
    base: str, query: LinkQuery, hidden: Container[str], carry: Mapping[str, str]
) -> str:
    """Return where "queue every match" posts, filter and all.

    Built from :func:`_link_url` so it is the same query string the links use,
    minus the page — which match is on which page has nothing to do with a set
    somebody is queueing whole — and minus the fragment, which a form action
    would only carry as far as the redirect.
    """
    listing = _link_url(base, query, hidden, carry, page=1).removesuffix("#links")
    _, separator, parameters = listing.partition("?")
    return f"{base}/downloads{separator}{parameters}"


def _downloadable_value(downloadable: bool | None) -> str:
    """Return the downloadable filter as the form spells it."""
    if downloadable is None:
        return ""
    return "yes" if downloadable else "no"


def _link_header(
    column: LinkColumn,
    query: LinkQuery,
    *,
    base: str,
    hidden: Container[str],
    carry: Mapping[str, str],
) -> dict[str, Any]:
    """Return one column heading, and the link that reorders by it.

    Clicking the active column reverses it; clicking another starts ascending,
    which for a URL and a plugin name is the direction anybody means. A column
    that cannot be ordered by is a heading and nothing more.
    """
    sort = column.sort
    if sort is None:
        return {
            "label": column.label,
            "name": column.name,
            "url": None,
            "active": False,
            "mark": "",
        }
    active = query.sort is sort
    descending = not query.descending if active else False
    return {
        "label": column.label,
        "name": column.name,
        "url": _link_url(base, query, hidden, carry, sort=sort, descending=descending, page=1),
        "active": active,
        "mark": SORT_MARKS[query.descending] if active else "",
    }


def _link_toggle(
    column: LinkColumn,
    query: LinkQuery,
    *,
    base: str,
    hidden: Container[str],
    carry: Mapping[str, str],
) -> dict[str, Any]:
    """Return the link that shows or hides one column.

    A link rather than a checkbox, so the whole control is the same mechanism as
    a facet chip: the state lives in the URL, the server renders the answer, and
    it works with scripting switched off. The one column that cannot be hidden
    is offered as a disabled entry rather than left out, because a control that
    silently lacks an entry reads as a bug.
    """
    is_shown = column.name not in hidden
    required = column.name == REQUIRED_COLUMN
    now_hidden = {name for name in _column_names() if name in hidden}
    if is_shown:
        now_hidden.add(column.name)
    else:
        now_hidden.discard(column.name)
    return {
        "label": column.label,
        "name": column.name,
        "shown": is_shown,
        "required": required,
        "url": None if required else _link_url(base, query, frozenset(now_hidden), carry),
    }


def _column_names() -> tuple[str, ...]:
    """Return every column name, which is what a hide list may contain."""
    return tuple(column.name for column in LINK_COLUMNS)


def _link_facets(
    page: LinkPage, *, base: str, hidden: Container[str], carry: Mapping[str, str]
) -> tuple[dict[str, Any], ...]:
    """Return the chip rows that filter by one value in one click.

    Counted over the whole crawl rather than over the matches, which is what the
    service already decided; what is added here is that the chip you are
    standing on links back to the unfiltered view, so a chip is a toggle rather
    than a one-way door.
    """
    query = page.query
    groups = (
        ("Plugin", page.plugins, "plugin", query.plugin, lambda value: value),
        (
            "Type",
            page.targets,
            "target",
            "" if query.target is None else str(query.target),
            lambda value: TARGET_LABELS[TargetKind(value)],
        ),
        ("Category", page.categories, "category", query.category, lambda value: value),
        ("State", page.states, "state", query.state, _link_state_label),
    )
    rows = []
    for heading, facets, name, active, label_of in groups:
        if not facets:
            continue
        rows.append(
            {
                "heading": heading,
                "chips": tuple(
                    {
                        "label": label_of(facet.value),
                        "count": format_number(facet.count),
                        "active": facet.value == active,
                        "url": _link_url(
                            base,
                            query,
                            hidden,
                            carry,
                            page=1,
                            **{name: None if facet.value == active else facet.value},
                        ),
                    }
                    for facet in facets
                ),
            }
        )
    return tuple(rows)


def _link_state_label(value: str) -> str:
    """Return what a state is called, or the value itself for one nobody named.

    Unlike the target kinds beside it, the states are an open set on purpose:
    the enum's own docstring promises that adding one costs a member, a resolver
    and a label. A missing label must therefore degrade to something legible
    rather than take the whole report down with a lookup error — the promise is
    only kept if the render side survives being the part that lags.
    """
    return LINK_STATE_LABELS.get(value, value)


def _hide_value(hidden: Container[str]) -> str:
    """Return the hidden columns as the query string writes them."""
    return ",".join(name for name in _column_names() if name in hidden)


def _link_url(
    base: str,
    query: LinkQuery,
    hidden: Container[str],
    carry: Mapping[str, str] = MappingProxyType({}),
    **changes: Any,
) -> str:
    """Return the report URL for *query* with *changes* applied.

    Only what differs from the default is written, so an untouched report is
    plain ``/crawls/{id}`` and a filtered one carries exactly what it needs. The
    fragment is always there: every one of these links leads to the table, and a
    reader who clicks a filter should not have to scroll back down to it.
    """
    target = changes.get("target", query.target)
    downloadable = changes.get("downloadable", query.downloadable)
    values = {
        "q": changes.get("search", query.search),
        "plugin": changes.get("plugin", query.plugin) or "",
        "category": changes.get("category", query.category) or "",
        "target": "" if target is None else str(target),
        "state": changes.get("state", query.state) or "",
        "dl": _downloadable_value(downloadable),
        "norm": "1" if changes.get("normalized_only", query.normalized_only) else "",
        "sort": str(changes.get("sort", query.sort)),
        "dir": "desc" if changes.get("descending", query.descending) else "asc",
        "page": str(changes.get("page", query.page)),
        "hide": _hide_value(hidden),
    }
    default = LinkQuery()
    if values["sort"] == str(default.sort):
        del values["sort"]
    if values["dir"] == "asc":
        del values["dir"]
    if values.get("page") == "1":
        del values["page"]
    written = {**carry, **{name: value for name, value in values.items() if value}}
    return f"{base}?{urlencode(written)}#links" if written else f"{base}#links"


def crawl_rows(
    crawls: Iterable[StoredCrawl], *, live: Container[str] = ()
) -> tuple[dict[str, Any], ...]:
    """Return one row per recorded crawl, for the lists that show history.

    Reads from the stored summary rather than from the job registry, because
    the registry is a live view that dies with the process and this is the part
    that should survive a restart.

    *live* names the crawls this process is actually running. A row is written
    as ``running`` when the database says unfinished *and* this process agrees;
    a row the database left unfinished that nobody is running is not running,
    it was abandoned when whatever started it stopped. Saying "running" about a
    crawl from last week would be a page waiting for something that will never
    arrive.
    """
    return tuple(_crawl_row(crawl, is_live=crawl.session_id in live) for crawl in crawls)


def _crawl_row(crawl: StoredCrawl, *, is_live: bool) -> dict[str, Any]:
    """Return one recorded crawl as a table row."""
    options = CrawlOptions(
        max_depth=crawl.max_depth,
        max_pages=max(1, crawl.max_pages),
        same_domain=crawl.same_domain,
        include_subdomains=crawl.include_subdomains,
        below_seed=crawl.below_seed,
        respect_robots=crawl.respect_robots,
    )
    unfinished = crawl.finished_at is None
    abandoned = unfinished and not is_live
    return {
        "job_id": crawl.session_id,
        "url": f"/crawls/{crawl.session_id}",
        "seed_url": crawl.seed_url,
        "state": str(crawl.state),
        "state_label": ABANDONED_LABEL if abandoned else STATE_LABELS[crawl.state],
        "state_tone": "bad" if abandoned else STATE_TONES[crawl.state],
        "options": describe_options(options),
        "started_at": format_timestamp(crawl.started_at),
        "finished_at": "—" if crawl.finished_at is None else format_timestamp(crawl.finished_at),
        "is_running": unfinished and is_live,
        "was_abandoned": abandoned,
        "pages_visited": format_number(crawl.pages_visited),
        "pages_failed": format_number(crawl.pages_failed),
        "has_failures": crawl.pages_failed > 0,
        "links_found": format_number(crawl.links_discovered),
        "elapsed": format_duration(crawl.elapsed_seconds),
    }


def stored_view(crawl: StoredCrawl, *, is_live: bool = False) -> dict[str, Any]:
    """Return what the page of a crawl this server did not run shows.

    Less than :func:`report_view`, and the difference is not an oversight: the
    database keeps a summary and the URLs, never the per-page outcomes or the
    reasons individual URLs were turned away. The page states that rather than
    showing empty tables that would read as "none".
    """
    row = _crawl_row(crawl, is_live=is_live)
    return {
        **row,
        "pages_attempted": format_number(crawl.pages_attempted),
        "pages_skipped": format_number(crawl.pages_skipped),
        "max_depth_reached": crawl.max_depth_reached,
        "frontier_remaining": format_number(crawl.frontier_remaining),
        "left_in_frontier": crawl.frontier_remaining > 0,
    }


def settings_view(settings: Settings) -> tuple[dict[str, Any], ...]:
    """Return the effective configuration, grouped the way it is thought about.

    Every value as a string. A settings page that formatted numbers one way and
    the TOML beside it another would invite exactly the confusion it exists to
    remove.
    """
    return (
        {
            "heading": "Identity",
            "rows": (_setting("user_agent", settings.user_agent, "Sent with every request."),),
        },
        {
            "heading": "Storage",
            "rows": (
                _setting(
                    "database_path",
                    settings.database_path.as_posix(),
                    "Where crawls and discovered URLs are recorded.",
                ),
                _setting(
                    "library_path",
                    settings.library_path.as_posix(),
                    "Where downloads are stored, one directory per resource.",
                ),
                _setting(
                    "max_view_bytes",
                    format_bytes(settings.max_view_bytes),
                    "Largest stored file the browser is offered inline.",
                ),
                _setting(
                    "max_stream_bytes",
                    "no limit"
                    if settings.max_stream_bytes <= 0
                    else format_size(settings.max_stream_bytes),
                    "Largest recording the browser is offered to play. Its own "
                    "limit because audio and video arrive in pieces.",
                ),
                _setting(
                    "preview_inline_bytes",
                    format_size(settings.preview_inline_bytes),
                    "Largest image a tile shows as itself. Above it a tile "
                    "shows a symbol, never a scaled-down original.",
                ),
                _setting(
                    "min_download_size",
                    format_size(settings.min_download_size),
                    "Smallest payload kept. Anything under it is recorded as "
                    "not kept, with both sizes, and never fetched again.",
                ),
                _setting("log_level", settings.log_level, ""),
                _setting("max_entries", format_number(settings.max_entries), ""),
            ),
        },
        {
            "heading": "Crawl defaults",
            "rows": (
                _setting(
                    "crawl_depth",
                    str(settings.crawl_depth),
                    "How far a crawl follows links unless told otherwise.",
                ),
                _setting(
                    "crawl_max_pages",
                    format_number(settings.crawl_max_pages),
                    "The ceiling every crawl is measured against.",
                ),
                _setting(
                    "crawl_same_domain",
                    _toml_bool(settings.crawl_same_domain),
                    "Off by default, so a share link to another host still works.",
                ),
                _setting(
                    "crawl_below_seed",
                    _toml_bool(settings.crawl_below_seed),
                    "The narrower scope: the start URL's path and nothing else.",
                ),
            ),
        },
        {
            # Its own heading rather than a line under "Crawl defaults": this
            # governs what may be *fetched*, which is the other half of the
            # program, and the headings are how a reader finds a setting.
            "heading": "Downloads",
            "rows": (
                _setting(
                    "direct_downloads",
                    _toml_bool(settings.direct_downloads),
                    "Whether a file at an ordinary URL may be downloaded at all.",
                ),
                _setting(
                    "max_queued",
                    format_number(settings.max_queued),
                    "How many requests may wait at once. Asking for more "
                    "queues what fits and says how many were left over.",
                ),
                _setting(
                    "download_workers",
                    str(settings.download_workers),
                    "How many transfers may be under way at once.",
                ),
                _setting(
                    "downloads_per_host",
                    str(settings.downloads_per_host),
                    "How many of those may be fetching from one host. What "
                    "keeps a pool polite when a crawl's take is all one site.",
                ),
            ),
        },
        {
            "heading": "Network",
            "rows": (
                _setting("network_timeout", f"{settings.network_timeout:g} s", ""),
                _setting("network_retries", str(settings.network_retries), ""),
                _setting(
                    "max_redirects",
                    str(settings.max_redirects),
                    "Hops one fetch may follow before the chain is called a loop.",
                ),
                _setting(
                    "max_page_bytes",
                    format_bytes(settings.max_page_bytes),
                    "Upper bound on one page, before and after decompression.",
                ),
                _setting(
                    "max_links",
                    format_number(settings.max_links),
                    "How many links one page may contribute.",
                ),
            ),
        },
        {
            "heading": "Responsible crawling",
            "rows": (
                _setting(
                    "respect_robots",
                    _toml_bool(settings.respect_robots),
                    "Obey each host's robots.txt. A URL it forbids is reported as skipped.",
                ),
                _setting(
                    "robots_user_agent",
                    settings.robots_user_agent or "(from user_agent)",
                    "Which product token robots.txt groups are matched against.",
                ),
                _setting(
                    "robots_timeout",
                    f"{settings.robots_timeout:g} s",
                    "How long to wait for a robots.txt before giving up on it.",
                ),
                _setting(
                    "robots_deny_on_error",
                    _toml_bool(settings.robots_deny_on_error),
                    "A host we could not reach is treated as forbidding everything.",
                ),
                _setting(
                    "crawl_delay",
                    f"{settings.crawl_delay:g} s",
                    "Waiting between requests to one host. Zero adds no delay of our own.",
                ),
                _setting(
                    "respect_crawl_delay",
                    _toml_bool(settings.respect_crawl_delay),
                    "Honour a Crawl-delay a host states for itself.",
                ),
                _setting(
                    "max_crawl_delay",
                    f"{settings.max_crawl_delay:g} s",
                    "The longest such delay obeyed, so one file cannot freeze a crawl.",
                ),
            ),
        },
        {
            "heading": "Private networks",
            "rows": (
                _setting(
                    "allow_private_networks",
                    _toml_bool(settings.allow_private_networks),
                    "Off, so a URL from a browser cannot reach this machine or this network.",
                ),
                _setting(
                    "private_network_allowlist",
                    ", ".join(settings.private_network_allowlist) or "(none)",
                    "Hosts, addresses or blocks exempt from that rule.",
                ),
            ),
        },
    )


def format_bytes(value: int) -> str:
    """Return a byte count the way the person who configured it wrote it."""
    for unit, size in (("MiB", 1024**2), ("KiB", 1024)):
        if value >= size and value % size == 0:
            return f"{value // size} {unit}"
    return f"{format_number(value)} bytes"


def _setting(name: str, value: str, explanation: str) -> dict[str, Any]:
    """Return one configured value as a row."""
    return {"name": name, "value": value, "explanation": explanation}


def _toml_bool(value: bool) -> str:
    """Return a boolean as the configuration file spells it."""
    return "true" if value else "false"


def page_rows(pages: Iterable[PageOutcome]) -> tuple[dict[str, Any], ...]:
    """Return one row per page the crawl reached, in the order it reached them."""
    return tuple(_page_row(page) for page in pages)


def _page_row(page: PageOutcome) -> dict[str, Any]:
    """Return one page as a table row."""
    return {
        "url": page.url,
        "final_url": page.final_url,
        "was_redirected": page.was_redirected,
        "depth": page.depth,
        "status": page.status,
        "status_label": "err" if page.status is None else str(page.status),
        "title": page.title,
        "canonical_url": page.canonical_url,
        "link_count": format_number(page.link_count),
        "error": page.error,
        "succeeded": page.succeeded,
    }


def link_rows(page: LinkPage) -> tuple[dict[str, Any], ...]:
    """Return one row per URL on *page*, in the order the service put them in."""
    return tuple(
        _link_row(item, downloadable=page.downloadable, known=page.known) for item in page.items
    )


def _state_marks(url: str, known: Mapping[LinkState, frozenset[str]]) -> tuple[dict[str, str], ...]:
    """Return the badges one row wears, or the one that says it wears none.

    Empty when nothing was asked, which is what withdraws the column entirely.
    Otherwise never empty: a row in no state gets the "new" badge rather than a
    blank cell. A blank would be the third meaning of an empty cell in this
    table — beside "the URL did not say" and "no plugin claimed it" — and the
    one thing a reader must be able to trust here is that no mark and *new* are
    the same sentence.

    Declared order rather than resolver order, so two reports of the same crawl
    put the same badges in the same places.
    """
    if not known:
        return ()
    marks = tuple(
        {"label": _link_state_label(str(state)), "tone": LINK_STATE_TONES.get(str(state), "")}
        for state in LinkState
        if url in known.get(state, frozenset())
    )
    return marks or ({"label": _link_state_label(UNTRACKED), "tone": LINK_STATE_TONES[UNTRACKED]},)


def _link_row(
    item: LinkItem,
    *,
    downloadable: Container[str] = (),
    known: Mapping[LinkState, frozenset[str]] = MappingProxyType({}),
) -> dict[str, Any]:
    """Return one recorded URL as a table row.

    ``plugin`` and ``category`` are where a URL nothing claimed gets its
    wording. The service leaves both as ``None``, because what to call an
    unanswered question is a decision for whoever is showing it — a terminal
    and a table legitimately word it differently.
    """
    return {
        "url": item.url,
        # The same link with its fragment dropped, for the checkbox's spoken
        # label. The key is in the cell beside it either way — a share link is
        # its key — but a screen reader announcing forty random characters
        # before every row is not how anybody finds the row they wanted.
        "spoken_url": strip_fragment(item.url),
        "raw_url": item.raw_url,
        "was_normalized": item.was_normalized,
        "source_url": item.source_url,
        "plugin": item.plugin or "unresolved",
        "category": item.category or "—",
        "target": TARGET_LABELS[item.target],
        # Whether the URL said anything at all, which is what decides emphasis:
        # "not stated" is true of most URLs and is not worth a reader's eye.
        "target_is_stated": item.target is not TargetKind.UNKNOWN,
        # True when a host-specific plugin claimed it rather than the fallback,
        # which is what the table gives its one piece of emphasis to.
        "is_notable": item.is_notable,
        # A link this installation could actually fetch. Not the same question
        # as "is it notable": a host-specific plugin can classify a link whose
        # provider cannot transfer anything.
        "can_download": item.url in downloadable,
        # What is known about this URL beyond the crawl having found it. A row
        # can wear several: a folder with one file stored and another queued is
        # in both states, and showing one of them would be choosing which half
        # of the truth to tell.
        "states": _state_marks(item.url, known),
    }


def _skip_rows(skips: Iterable[tuple[SkipReason, int]]) -> tuple[dict[str, Any], ...]:
    """Return why URLs were turned away, most frequent first."""
    return tuple({"reason": str(reason), "count": format_number(count)} for reason, count in skips)


def _kind_rows(kinds: Iterable[tuple[LinkKind, int]]) -> tuple[dict[str, Any], ...]:
    """Return how links were written, in the order the report lists them."""
    return tuple(
        {"kind": KIND_LABELS[kind], "count": format_number(count)} for kind, count in kinds
    )


def _state_label(snapshot: JobSnapshot) -> str:
    """Return the badge text for a snapshot, error included."""
    if snapshot.error is not None:
        return "failed"
    return STATE_LABELS[snapshot.state]


def _state_tone(snapshot: JobSnapshot) -> str:
    """Return the style class for a snapshot, error included."""
    if snapshot.error is not None:
        return "bad"
    return STATE_TONES[snapshot.state]


def maintenance_view(runs: Iterable[MaintenanceRun], box: Toolbox) -> tuple[dict[str, Any], ...]:
    """Return one card per maintenance script, each with the lines to paste.

    Two lines where a run writes, and the flag is the only difference between
    them: a pass over a real library is a list worth reading before it is a
    thing worth doing. One line where it does not write, because there is
    nothing to read first.

    Without a scripts directory — an installation from a wheel — the cards keep
    their descriptions and lose their commands. Naming a file that was never
    copied would be worse than saying there is nothing to run.
    """
    return tuple(_maintenance_card(run, box) for run in runs)


def _maintenance_card(run: MaintenanceRun, box: Toolbox) -> dict[str, Any]:
    """Return one card, commands and all."""
    slug = run.script.removesuffix(".py").replace("_", "-")
    lines: list[dict[str, Any]] = []
    dry = box.command(run)
    if dry is not None:
        lines.append(
            {
                "label": "What it would do" if run.writes else "Run it",
                "id": f"command-{slug}",
                "command": dry,
            }
        )
        if run.writes:
            applied = box.command(run, apply=True)
            lines.append({"label": "Do it", "id": f"command-{slug}-apply", "command": applied})
    return {
        "script": run.script,
        "title": run.title,
        "summary": run.summary,
        "caution": run.caution,
        # Named as the command that installs it, since that is what somebody
        # reading this would do next.
        "extra": None if run.extra is None else f"uv sync --extra {run.extra}",
        "writes": run.writes,
        "commands": tuple(lines),
    }


def cache_view(usage: CacheUsage, *, available: bool) -> dict[str, Any]:
    """Return what the thumbnail cache holds, for the line above the cards.

    The one fact about this installation that no other page carries, and the one
    worth knowing before running the maker: whether it can run at all, and how
    much is already there.
    """
    return {
        "root": usage.root.as_posix(),
        "count": format_number(usage.count),
        "size": format_size(usage.total_bytes),
        "empty": usage.count == 0,
        "available": available,
    }
