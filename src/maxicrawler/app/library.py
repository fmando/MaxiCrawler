"""Reading the library, for whoever is asking.

Downloading and browsing are different questions about the same store.
:class:`~maxicrawler.app.downloading.DownloadService` writes into the library;
this service reads it, and is where searching, filtering, sorting and paging
live so that a browser and a future ``library list`` command cannot disagree
about what "sorted by name" means.

**The file system is the authority; the index is a cache in front of it.** Every
entry describes itself (ADR-010), and reading one small document per stored
resource is what a listing used to cost. That cost was measured rather than
assumed: on the machine this was written on, two thousand entries take about 0.3
seconds once the directory is warm in the operating system's cache, and roughly
sixteen seconds the very first time — the antivirus scanner reads every file
before Python does. Paging does not help, because searching and sorting need
every record.

So a listing now consults
:class:`~maxicrawler.database.library.SQLiteLibraryIndex` and reads only the
documents that changed. Every entry is still *stat*-ed on every listing, which is
what makes the cache safe to believe: a row is trusted only while the document it
came from has the same modification time and size. The saving is the parse, not
the walk.

Three rules keep the cache from quietly becoming the authority, and each of them
is a property this module can be read for:

* **Only set questions go through it.** :meth:`LibraryService.browse` does;
  :meth:`LibraryService.item` and :meth:`LibraryService.payload` do not, and read
  the entry's own directory as they always have. A stale row can therefore delay
  a listing and can never serve the wrong file.
* **A database that cannot be used is not an error.** Every failure of the index
  falls back to reading the file system, which is the behaviour this service had
  before the index existed and still has when none is supplied.
* **It is built here, not by a client.** The cache lives in the metadata
  database the settings already name, and it is assembled by this service for
  the same reason
  :meth:`~maxicrawler.app.discovery.DiscoveryService._repository` assembles its
  own: a client that built one would be building a second object graph, which
  ``tests/test_api_boundaries.py`` forbids by name. An index may still be handed
  in, which is how a test points one at a database of its own.

**A damaged entry is skipped, never raised.** One directory holding unreadable
JSON must not be able to empty the page that would have shown the other nine
hundred. What it costs is that a broken entry is invisible until somebody looks
at the directory; what it buys is that a library stays browsable while it is
being repaired.
"""

import json
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import ceil
from pathlib import Path
from typing import Any

from maxicrawler.app.viewing import MediaVerdict, verdict_for
from maxicrawler.config import Settings
from maxicrawler.database import IndexedEntry, SQLiteDatabase, SQLiteLibraryIndex
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

    def __init__(
        self,
        settings: Settings,
        *,
        library: Library | None = None,
        index: SQLiteLibraryIndex | None = None,
    ) -> None:
        self._settings = settings
        self._library = library if library is not None else Library(settings.library_path)
        self._injected_index = index
        self._cached_index: SQLiteLibraryIndex | None = None
        self._index_unavailable = False
        self._root_key = _root_key(self._library.root)

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
        """Yield every entry that describes itself readably.

        Through the index when there is one, and straight off the file system
        when there is not — or when the database refuses, which is not a failure
        anybody should have to see. A cache is allowed to be unavailable.
        """
        index = self._index()
        if index is None:
            yield from self._read_entries()
            return
        try:
            cached = self._synchronize(index)
        except sqlite3.Error:
            yield from self._read_entries()
            return
        for row in cached:
            item = self._item_of(row)
            if item is not None:
                yield item

    def _index(self) -> SQLiteLibraryIndex | None:
        """Return the cache to consult, building it once, or ``None``.

        Built here rather than handed in by the web application, for the reason
        :meth:`~maxicrawler.app.discovery.DiscoveryService._repository` is: the
        clients may not assemble an object graph of their own, and
        ``tests/test_api_boundaries.py`` enforces that by name. Cached rather
        than rebuilt per call, so the ``CREATE TABLE IF NOT EXISTS`` happens once
        instead of on every request.

        A database that cannot be opened at all disables the cache for the life
        of this service instead of being retried on every listing. The library
        is still fully readable without it; that is the point of it being a
        cache, and a page that stalls on a broken database every time it is
        loaded would be a worse failure than the slow listing it replaced.
        """
        if self._injected_index is not None:
            return self._injected_index
        if self._index_unavailable:
            return None
        if self._cached_index is None:
            index = SQLiteLibraryIndex(SQLiteDatabase(self._settings.database_path))
            try:
                index.initialize()
            except sqlite3.Error:
                self._index_unavailable = True
                return None
            self._cached_index = index
        return self._cached_index

    def _read_entries(self) -> Iterator[LibraryItem]:
        """Yield every entry, reading each document from its own directory."""
        for entry in self._library.entries():
            item = _item(entry)
            if item is not None:
                yield item

    def _synchronize(self, index: SQLiteLibraryIndex) -> tuple[IndexedEntry, ...]:
        """Bring the cache level with the directories, and return what it holds.

        One pass over the entries, one ``stat`` each, and a read only where the
        document is new or has changed since it was cached. Entries whose
        directory has gone are dropped in the same transaction as the additions,
        so no listing sees a resource in two places at once.

        The walk itself is not saved and is not meant to be: it reads directory
        names only (ADR-010), and it is what keeps the answer true when somebody
        adds a library entry with ``rsync`` rather than with a download.
        """
        cached = index.entries(self._root_key)
        present: set[tuple[str, str]] = set()
        updated: list[IndexedEntry] = []
        for entry in self._library.entries():
            stamp = _stamp(entry.metadata_path)
            if stamp is None:
                # No metadata document at all: a directory somebody created by
                # hand is not an entry, which is the rule `_item` already keeps.
                continue
            identity = (entry.provider, entry.key)
            present.add(identity)
            known = cached.get(identity)
            if known is not None and known.describes(*stamp):
                continue
            fresh = _indexed(entry, stamp)
            if fresh is None:
                continue
            cached[identity] = fresh
            updated.append(fresh)
        removed = tuple(identity for identity in cached if identity not in present)
        for identity in removed:
            del cached[identity]
        if updated or removed:
            index.refresh(self._root_key, updated=updated, removed=removed)
        return tuple(cached.values())

    def _item_of(self, row: IndexedEntry) -> LibraryItem | None:
        """Return one cached row as a listed item, or ``None`` when it says nothing.

        The document is parsed here rather than in the adapter, because what a
        metadata document means is this layer's business and changes when the
        library's schema does. A row that cannot be parsed is skipped exactly as
        an unreadable file is: see the module docstring.
        """
        record = _record_of(row.document)
        if record is None:
            return None
        return _item_from_record(
            record,
            directory=row.directory,
            key=row.key,
            path=self._library.root / row.directory / row.key,
        )


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
    return _item_from_record(record, directory=entry.provider, key=entry.key, path=entry.path)


def _item_from_record(
    record: ResourceRecord, *, directory: str, key: str, path: Path
) -> LibraryItem:
    """Return the item *record* describes, given where its entry lives.

    Split from :func:`_item` so that a record read from a directory and one read
    from the index become the same listed row. The three arguments are what the
    document itself does not say: a record names its provider but not the
    directory that provider was written to, and knows nothing about where the
    library it is in has been mounted.
    """
    content = record.content
    return LibraryItem(
        provider=record.provider,
        directory=directory,
        key=key,
        name=_name(record, key),
        status=record.status,
        source_url=record.source_url,
        filename=None if content is None else content.filename,
        size=None if content is None else content.size,
        path=None if content is None else path / content.path,
        checksum=None if content is None else content.checksum("sha256"),
        discovered_at=record.discovered_at,
        downloaded_at=record.downloaded_at,
        attempts=record.attempts,
        error=record.error,
    )


def _name(record: ResourceRecord, key: str) -> str:
    """Return what to call this resource.

    The recorded name, then the stored file name, then the entry key. A share
    published without its key has no readable name at all, and the key is at
    least what the directory is called.
    """
    if record.name:
        return record.name
    if record.content is not None and record.content.filename:
        return record.content.filename
    return key


def _root_key(root: Path) -> str:
    """Return what addresses one library inside the shared cache table.

    Resolved, so a library reached as ``library`` and as ``./library`` is one
    library rather than two sets of rows that each look complete. Not resolved
    strictly: the directory does not have to exist to be named, and a library
    that has not been created yet is an ordinary state.
    """
    try:
        return str(root.resolve(strict=False))
    except OSError:
        return str(root)


def _stamp(path: Path) -> tuple[int, int] | None:
    """Return the modification time and size of *path*, or ``None``.

    The whole basis for believing a cached row, and deliberately two values
    rather than one: a modification time has a resolution, and a document
    rewritten within it is caught by its length changing.
    """
    try:
        status = path.stat()
    except OSError:
        return None
    return (status.st_mtime_ns, status.st_size)


def _indexed(entry: LibraryEntry, stamp: tuple[int, int]) -> IndexedEntry | None:
    """Return the cache row for *entry*, or ``None`` when it cannot be read.

    A document that reads but does not parse still becomes a row, with its
    columns left empty. That looks odd until you consider the alternative: no
    row means it is read again on every listing, forever, and a library with one
    corrupt entry would pay for it on every page. Stored, it is skipped once per
    change rather than once per request — and :meth:`LibraryService._item_of`
    leaves it out of the listing either way.
    """
    document = _read_document(entry.metadata_path)
    if document is None:
        return None
    record = _record_of(document)
    content = None if record is None else record.content
    return IndexedEntry(
        directory=entry.provider,
        key=entry.key,
        mtime_ns=stamp[0],
        size=stamp[1],
        document=document,
        source_url="" if record is None else record.source_url,
        status="" if record is None else record.status.value,
        checksum=None if content is None else content.checksum("sha256"),
    )


def _read_document(path: Path) -> str | None:
    """Return the text of *path*, or ``None`` when it cannot be read."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _record_of(document: str) -> ResourceRecord | None:
    """Return the record *document* describes, or ``None`` when it describes none.

    Every way of being unreadable answers the same way, for the reason
    :meth:`LibraryService.item` gives: a caller has one thing to say about a
    document that is not JSON, one that is JSON but not an object, and one
    written by a release this one does not understand.
    """
    try:
        parsed = json.loads(document)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    try:
        return ResourceRecord.from_document(parsed)
    except LibraryError:
        return None


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
