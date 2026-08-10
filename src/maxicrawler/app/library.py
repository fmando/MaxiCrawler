"""Reading the library, for whoever is asking.

Downloading and browsing are different questions about the same store.
:class:`~maxicrawler.app.downloading.DownloadService` writes into the library;
this service reads it, and is where searching, filtering, sorting and paging
live so that a browser and a future ``library list`` command cannot disagree
about what "sorted by name" means.

**The file system is the index.** Every entry describes itself (ADR-010), so a
query reads one small document per stored resource and no database. That is a
deliberate cost, and it is measured rather than assumed: on the machine this was
written on, two thousand entries take about 0.3 seconds once the directory is
warm in the operating system's cache, and roughly sixteen seconds the very first
time — the antivirus scanner reads every file before Python does. Paging does not
help, because searching and sorting need every record. What would help is an
index kept as a *cache*, invalidated by modification time, and ADR-010 already
says that is allowed while making the file system the authority. It is not built
yet because a library of a few dozen entries does not notice, and a cache nobody
needs is a source of stale answers.

**A damaged entry is skipped, never raised.** One directory holding unreadable
JSON must not be able to empty the page that would have shown the other nine
hundred. What it costs is that a broken entry is invisible until somebody looks
at the directory; what it buys is that a library stays browsable while it is
being repaired.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import ceil
from pathlib import Path
from typing import Any

from maxicrawler.app.viewing import MediaVerdict, verdict_for
from maxicrawler.config import Settings
from maxicrawler.domain import DownloadStatus
from maxicrawler.library import Library, LibraryEntry, LibraryError, ResourceRecord

DEFAULT_PER_PAGE = 50
"""How many stored resources one page of the library shows."""

MAX_PER_PAGE = 200
"""Ceiling on what a caller may ask for in one page.

A request for ten thousand rows is either a mistake or an attempt to make the
server render for a minute, and neither deserves to be honoured.
"""


class LibrarySort(StrEnum):
    """Which column a listing is ordered by."""

    NAME = "name"
    SIZE = "size"
    DOWNLOADED = "downloaded"
    PROVIDER = "provider"
    STATUS = "status"

    @classmethod
    def parse(cls, value: str | None, *, default: "LibrarySort") -> "LibrarySort":
        """Return the sort *value* names, or *default* when it names none.

        Lenient on purpose: the value arrives in a query string, where a stale
        bookmark or a typed URL is ordinary. A listing in the wrong order is a
        worse answer than the default order, but a refusal is worse than both.
        """
        try:
            return cls(value or "")
        except ValueError:
            return default


@dataclass(frozen=True, slots=True)
class LibraryItem:
    """One stored resource, as a client lists it.

    Read from the entry's own metadata document rather than from an index,
    because the file system is the library's source of truth (ADR-010) and a
    listing that disagreed with it would be worse than no listing.

    ``provider`` is what a person is shown, taken from the record; ``directory``
    is what addresses the entry in a URL, taken from the layout. They usually
    read the same and are not the same thing: a provider is free to call itself
    ``GoFile``, and only one of the two forms is safe in a path.
    """

    provider: str
    directory: str
    key: str
    name: str
    status: DownloadStatus
    source_url: str
    filename: str | None = None
    size: int | None = None
    path: Path | None = None
    """Where the payload is, when the record claims one. Not checked here."""

    checksum: str | None = None
    discovered_at: datetime | None = None
    downloaded_at: datetime | None = None
    attempts: int = 0
    error: str | None = None

    @property
    def is_stored(self) -> bool:
        """Return whether this record claims a finished payload."""
        return self.status is DownloadStatus.COMPLETED and self.path is not None


@dataclass(frozen=True, slots=True)
class StoredPayload:
    """A file that is really there, and what may be done with it.

    Produced only after the path has been checked, so a caller holding one of
    these needs to decide nothing further: it exists, it is inside the library,
    and :attr:`media` says whether a browser may be shown it.
    """

    path: Path
    filename: str
    size: int
    media: MediaVerdict


@dataclass(frozen=True, slots=True)
class LibraryQuery:
    """What a caller wants to see of the library."""

    search: str = ""
    """Matched against the name, the stored file name and the source URL."""

    provider: str | None = None
    """A provider *directory*, because that is what a URL can carry safely."""

    status: DownloadStatus | None = None
    sort: LibrarySort = LibrarySort.DOWNLOADED
    descending: bool = True
    page: int = 1
    per_page: int = DEFAULT_PER_PAGE

    @property
    def is_filtered(self) -> bool:
        """Return whether this query shows less than the whole library."""
        return bool(self.search) or self.provider is not None or self.status is not None


@dataclass(frozen=True, slots=True)
class LibraryPage:
    """One page of a listing, and enough about the rest to navigate it."""

    items: tuple[LibraryItem, ...]
    query: LibraryQuery
    total: int
    """How many entries matched the query."""

    stored: int
    """How many the library holds altogether, matched or not."""

    page: int
    pages: int
    providers: tuple[str, ...]
    """The provider directories present, whatever the query asked for.

    The whole library rather than the matches, so choosing a filter never
    removes the entry you would use to choose a different one.
    """

    statuses: tuple[DownloadStatus, ...] = ()
    """The verdicts present, on the same terms and for the same reason.

    Derived rather than listed from the enum: offering "running" as a filter for
    a store that never holds one is a menu entry that can only disappoint.
    """

    @property
    def first(self) -> int:
        """Return the one-based index of the first row shown, or zero."""
        return (self.page - 1) * self.query.per_page + 1 if self.items else 0

    @property
    def last(self) -> int:
        """Return the one-based index of the last row shown, or zero."""
        return (self.page - 1) * self.query.per_page + len(self.items)

    @property
    def has_previous(self) -> bool:
        """Return whether there is a page before this one."""
        return self.page > 1

    @property
    def has_next(self) -> bool:
        """Return whether there is a page after this one."""
        return self.page < self.pages


class LibraryService:
    """Everything a client needs to browse the library, and nothing about showing it."""

    def __init__(self, settings: Settings, *, library: Library | None = None) -> None:
        self._settings = settings
        self._library = library if library is not None else Library(settings.library_path)

    @property
    def settings(self) -> Settings:
        """Return the settings this service reads its limits from."""
        return self._settings

    @property
    def library_root(self) -> Path:
        """Return the directory the library occupies."""
        return self._library.root

    def browse(self, query: LibraryQuery | None = None) -> LibraryPage:
        """Return the page of the library *query* asks for.

        Every record is read, then filtered, then sorted, then cut to a page —
        in that order, because any other one gives a different answer. Sorting
        after paging would order a page instead of the library.
        """
        asked = query if query is not None else LibraryQuery()
        items = tuple(self._items())
        matching = tuple(item for item in items if _matches(item, asked))
        ordered = _ordered(matching, asked)
        per_page = min(max(asked.per_page, 1), MAX_PER_PAGE)
        pages = max(1, ceil(len(ordered) / per_page))
        page = min(max(asked.page, 1), pages)
        start = (page - 1) * per_page
        return LibraryPage(
            items=ordered[start : start + per_page],
            query=asked,
            total=len(ordered),
            stored=len(items),
            page=page,
            pages=pages,
            providers=tuple(sorted({item.directory for item in items})),
            statuses=tuple(sorted({item.status for item in items})),
        )

    def item(self, provider: str, key: str) -> LibraryItem | None:
        """Return the stored resource *provider*/*key* names, if there is one.

        ``None`` covers every way of not having it: a name that could not be a
        component, a directory that is not there, and a document that cannot be
        read. A caller answering a request has one thing to say about all three.
        """
        entry = self._library.entry_at(provider, key)
        return None if entry is None else _item(entry)

    def payload(self, provider: str, key: str) -> StoredPayload | None:
        """Return the file *provider*/*key* holds, if it is really there.

        ``None`` when the entry is unknown, when the record claims no payload,
        and when the record claims one that has since been deleted or moved.
        The third case is why this exists rather than a caller reading
        :attr:`LibraryItem.path`: a library is repairable, and a page that
        offered a file which is not there would be the wrong kind of certain.
        """
        item = self.item(provider, key)
        if item is None or item.path is None or item.filename is None:
            return None
        try:
            if not item.path.is_file():
                return None
            size = item.path.stat().st_size
        except OSError:
            return None
        return StoredPayload(
            path=item.path,
            filename=item.filename,
            size=size,
            media=verdict_for(item.filename, size, max_bytes=self._settings.max_view_bytes),
        )

    def _items(self) -> Iterator[LibraryItem]:
        """Yield every entry that describes itself readably."""
        for entry in self._library.entries():
            item = _item(entry)
            if item is not None:
                yield item


def _item(entry: LibraryEntry) -> LibraryItem | None:
    """Return *entry* as a listed item, or ``None`` when it says nothing.

    An entry with no metadata at all is not an item — a directory somebody
    created by hand is not a download. An entry whose metadata is unreadable is
    not one either, and deliberately does not raise: see the module docstring.
    """
    try:
        record = entry.read()
    except LibraryError:
        return None
    if record is None:
        return None
    content = record.content
    return LibraryItem(
        provider=record.provider,
        directory=entry.provider,
        key=entry.key,
        name=_name(record, entry),
        status=record.status,
        source_url=record.source_url,
        filename=None if content is None else content.filename,
        size=None if content is None else content.size,
        path=None if content is None else entry.path / content.path,
        checksum=None if content is None else content.checksum("sha256"),
        discovered_at=record.discovered_at,
        downloaded_at=record.downloaded_at,
        attempts=record.attempts,
        error=record.error,
    )


def _name(record: ResourceRecord, entry: LibraryEntry) -> str:
    """Return what to call this resource.

    The recorded name, then the stored file name, then the entry key. A share
    published without its key has no readable name at all, and the key is at
    least what the directory is called.
    """
    if record.name:
        return record.name
    if record.content is not None and record.content.filename:
        return record.content.filename
    return entry.key


def _matches(item: LibraryItem, query: LibraryQuery) -> bool:
    """Return whether *item* belongs in the answer to *query*."""
    if query.provider is not None and item.directory != query.provider:
        return False
    if query.status is not None and item.status is not query.status:
        return False
    if not query.search:
        return True
    needle = query.search.casefold()
    haystack = (item.name, item.filename or "", item.source_url)
    return any(needle in value.casefold() for value in haystack)


def _ordered(items: tuple[LibraryItem, ...], query: LibraryQuery) -> tuple[LibraryItem, ...]:
    """Return *items* in the order *query* asks for."""
    return tuple(sorted(items, key=lambda item: _sort_key(item, query), reverse=query.descending))


def _sort_key(item: LibraryItem, query: LibraryQuery) -> tuple[Any, ...]:
    """Return what one item sorts by.

    Every ordering ends in the entry's own identity, so two files with the same
    name never swap places between two requests. A value nobody recorded sorts
    last however the direction runs: "unknown" is not a small size, and putting
    it at the top of a descending list would read as one.
    """
    absent = _absent(query.descending)
    match query.sort:
        case LibrarySort.NAME:
            primary: tuple[Any, ...] = (item.name.casefold(),)
        case LibrarySort.SIZE:
            primary = (absent(item.size is None), item.size or 0)
        case LibrarySort.PROVIDER:
            primary = (item.provider.casefold(),)
        case LibrarySort.STATUS:
            primary = (item.status.value,)
        case _:
            primary = (
                absent(item.downloaded_at is None),
                item.downloaded_at or _EPOCH,
            )
    return (*primary, item.directory, item.key)


def _absent(descending: bool) -> Callable[[bool], int]:
    """Return the ranking that keeps an unrecorded value last in either direction.

    The rank is flipped for a descending sort, because the reversal that puts the
    largest file first would otherwise put "size unknown" there too.
    """
    return lambda is_absent: int(is_absent) if not descending else int(not is_absent)


_EPOCH = datetime.min.replace(tzinfo=UTC)
"""Stand-in for a timestamp nobody recorded; never compared with a real one."""
