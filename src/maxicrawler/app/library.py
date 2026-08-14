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
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from math import ceil
from pathlib import Path
from typing import Any

from maxicrawler.app.discovery import StateResolver
from maxicrawler.app.viewing import MediaKind, MediaVerdict, kind_for, verdict_for
from maxicrawler.config import Settings
from maxicrawler.database import IndexedEntry, SQLiteDatabase, SQLiteLibraryIndex
from maxicrawler.domain import DownloadStatus
from maxicrawler.library import (
    ContentRecord,
    Library,
    LibraryEntry,
    LibraryError,
    ResourceRecord,
)
from maxicrawler.utils import strip_fragment

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
    kind: MediaKind = MediaKind.OTHER
    """What sort of file this is, from its name.

    Read from the stored file name where there is one and from the recorded
    name where there is not, so an entry that never received a payload — a
    failure, or something a limit turned away — still sorts under the category
    a person would look for it in.
    """

    queued: bool = False
    """Whether something is waiting or running right now to fetch this again.

    Not a fourth download status and not stored anywhere: the queue lives in one
    process's memory, the record lives on disk, and this is the moment the two
    are put beside each other. A retry in progress is why a row would otherwise
    still read "failed", which is yesterday's truth told confidently.
    """

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
    kind: MediaKind | None = None
    min_size: int | None = None
    """Smallest payload to show, in bytes; ``None`` for no lower bound."""

    max_size: int | None = None
    """Largest payload to show, in bytes; ``None`` for no upper bound.

    An entry whose size nobody recorded is kept by an *unbounded* query and
    dropped by a bounded one. "Between 1 and 10 MB" is a claim about a number,
    and a row with no number cannot satisfy it — while showing it anyway would
    put the same unmeasured file in "under 1 MB" and in "over 100 MB" both.
    """

    queued: bool = False
    """Show only what is waiting or running in the transfer queue right now.

    Answers with nothing when no queue was handed to the service — the command
    line, and any test that builds one bare. A client that cannot ask a queue
    knows of nothing in one, and saying "everything, then" would be a filter
    that quietly does not filter.
    """

    sort: LibrarySort = LibrarySort.DOWNLOADED
    descending: bool = True
    page: int = 1
    per_page: int = DEFAULT_PER_PAGE

    @property
    def is_filtered(self) -> bool:
        """Return whether this query shows less than the whole library."""
        return (
            bool(self.search)
            or self.provider is not None
            or self.status is not None
            or self.kind is not None
            or self.min_size is not None
            or self.max_size is not None
            or self.queued
        )


@dataclass(frozen=True, slots=True)
class LibraryFacet:
    """One value a listing can be narrowed to, and how much of it there is.

    The same shape :class:`~maxicrawler.app.discovery.LinkFacet` has, because a
    report's chips and a library's chips are the same control asked about two
    different stores, and one of them having a count the other lacks would show
    up as two designs on two pages.
    """

    value: str
    count: int


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
    providers: tuple[LibraryFacet, ...]
    """The provider directories present, and how many entries each holds.

    Counted over the **whole library** rather than over the matches, which is
    two decisions in one and both deliberate.

    The *values* are the whole library so that choosing a filter never removes
    the entry you would use to choose a different one — a chip row that shrank
    as you used it would be a door that locks behind you.

    The *counts* are the whole library because that is what a report's chips
    already say (:func:`~maxicrawler.app.discovery._plugin_facets` counts over
    everything recorded), and one rule across the product beats a better rule on
    one page of it. What it costs is real and worth writing down: a chip saying
    twelve can answer with an empty table once a search is on, because it counts
    twelve in the library rather than twelve in what you are looking at.
    """

    statuses: tuple[LibraryFacet, ...] = ()
    """The verdicts present, on the same terms and for the same reason.

    Derived rather than listed from the enum: offering "running" as a filter for
    a store that never holds one is a menu entry that can only disappoint.
    """

    kinds: tuple[LibraryFacet, ...] = ()
    """The categories present, derived for the reason :attr:`statuses` is.

    A library of nothing but images should not offer to show only the videos.
    """

    queued: int | None = None
    """How many entries something is queued for, or ``None`` if nobody can say.

    Absent rather than zero when no queue was handed in, the distinction
    :attr:`~maxicrawler.app.discovery.LinkPage.known` draws for the same reason:
    "none are queued" and "there is no queue here to ask" are different answers,
    and only one of them should put a chip on the page.
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
        queued: StateResolver | None = None,
    ) -> None:
        self._settings = settings
        self._library = library if library is not None else Library(settings.library_path)
        self._queued = queued
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
        items = self._marked(tuple(self._items()))
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
            providers=_facets(items, lambda item: item.directory, sorted),
            statuses=_facets(items, lambda item: item.status.value, sorted),
            kinds=_facets(items, lambda item: item.kind.value, _kind_order),
            # Counted over the whole library like every facet beside it, and
            # left absent rather than zero when nothing can answer.
            queued=None if self._queued is None else sum(item.queued for item in items),
        )

    def item(self, provider: str, key: str) -> LibraryItem | None:
        """Return the stored resource *provider*/*key* names, if there is one.

        ``None`` covers every way of not having it: a name that could not be a
        component, a directory that is not there, and a document that cannot be
        read. A caller answering a request has one thing to say about all three.
        """
        entry = self._library.entry_at(provider, key)
        if entry is None:
            return None
        item = _item(entry)
        return None if item is None else self._marked((item,))[0]

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

    def stored(self, urls: Iterable[str]) -> frozenset[str]:
        """Return which of *urls* the library holds something fetched from.

        The answer a report marks its rows with, and the shape
        :data:`~maxicrawler.app.discovery.StateResolver` asks for: a set of the
        URLs *as they were given*, so the caller can match them against what it
        already has.

        Fragments are stripped before comparing and kept in the answer. A share
        link carries its decryption key there, a stored record never does
        (ADR-020), and the two would otherwise never compare equal — while a
        caller handed back a key-less URL would have lost the only thing that
        makes the link usable.

        This is a claim about the URL, not about completeness: a share naming a
        folder is recorded by each file inside it, under the container's URL, so
        one stored file puts the container in this answer. What that is called
        where somebody reads it is the caller's decision, and
        :class:`~maxicrawler.app.discovery.LinkState` is where it is made.

        Asked in bulk because the cost is one pass over the library, not one per
        URL — and the cost of a pass is what the index exists to keep small: the
        source URLs are a column on it, so this reads no metadata document at all.
        """
        asked = tuple(urls)
        if not asked:
            return frozenset()
        known = self._source_urls()
        return frozenset(url for url in asked if strip_fragment(url) in known)

    def _source_urls(self) -> frozenset[str]:
        """Return every URL the library has stored something from.

        Off the index's own column where there is one, which reads no document
        and parses no JSON, and off the entries themselves where there is not.
        """
        index = self._index()
        if index is not None:
            try:
                rows = self._synchronize(index)
            except sqlite3.Error:
                pass
            else:
                return frozenset(row.source_url for row in rows if row.source_url)
        return frozenset(item.source_url for item in self._read_entries())

    def _marked(self, items: tuple[LibraryItem, ...]) -> tuple[LibraryItem, ...]:
        """Return *items* with those the queue is working on flagged.

        Asked in bulk for the reason :meth:`stored` is asked in bulk: the answer
        costs one pass over a set, and one question per row would turn a listing
        into a thousand.

        The service never learns that there *is* a queue. It is handed a function
        over URLs — the shape :data:`~maxicrawler.app.discovery.StateResolver`
        names — exactly as the report is handed one to answer "already in the
        library", and this is the same arrangement pointed the other way. Without
        one, nothing is flagged and nothing here allocates.
        """
        if self._queued is None or not items:
            return items
        waiting = frozenset(self._queued({item.source_url for item in items}))
        if not waiting:
            return items
        return tuple(
            replace(item, queued=True) if item.source_url in waiting else item for item in items
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
    name = _name(record, key)
    return LibraryItem(
        provider=record.provider,
        directory=directory,
        key=key,
        name=name,
        kind=_kind_of(content, name),
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


def _facets(
    items: tuple[LibraryItem, ...],
    value_of: Callable[[LibraryItem], str],
    order: Callable[[Iterable[str]], list[str]],
) -> tuple[LibraryFacet, ...]:
    """Return how many of *items* each value of *value_of* accounts for.

    One pass, and the ordering handed in rather than assumed: a provider list
    reads best alphabetically and a category list does not (see
    :func:`_kind_order`).
    """
    counts: dict[str, int] = {}
    for item in items:
        value = value_of(item)
        counts[value] = counts.get(value, 0) + 1
    return tuple(LibraryFacet(value=value, count=counts[value]) for value in order(counts))


def _kind_order(values: Iterable[str]) -> list[str]:
    """Return the categories in the order they are offered in.

    Declaration order rather than alphabetical, which is the one place this
    differs from the providers and the statuses beside it. The members are
    declared in the order somebody sorting through a crawl reaches for them —
    pictures and video first, "other" last — and sorting the values as strings
    would put "archive" at the top and scatter the rest by spelling.
    """
    present = set(values)
    return [kind.value for kind in MediaKind if kind.value in present]


def _kind_of(content: ContentRecord | None, name: str) -> MediaKind:
    """Return the category of an entry holding *content* and called *name*.

    The stored file name decides it, because that is what the payload actually
    is. An entry with no payload falls back to the recorded name — a download
    that failed, or one a limit turned away, is still a picture or an archive as
    far as somebody looking for it is concerned, and it would otherwise sit in
    "other" where nobody would think to look.

    The fallback also covers a payload whose own suffix says nothing, which is
    how a provider that stored ``download`` beside a record named ``holiday.jpg``
    stays findable.
    """
    if content is not None:
        stored = kind_for(content.filename)
        if stored is not MediaKind.OTHER:
            return stored
    return kind_for(name)


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
    if query.kind is not None and item.kind is not query.kind:
        return False
    if query.queued and not item.queued:
        return False
    if not _within_size(item.size, query):
        return False
    if not query.search:
        return True
    needle = query.search.casefold()
    haystack = (item.name, item.filename or "", item.source_url)
    return any(needle in value.casefold() for value in haystack)


def _within_size(size: int | None, query: LibraryQuery) -> bool:
    """Return whether *size* falls inside the bounds *query* asks for.

    An unmeasured size fails any bound and satisfies none, which is the only
    reading that keeps the ranges a partition: counted as small it would appear
    under "under 1 MB", counted as large under "over 100 MB", and counted as
    both it would be in two buckets at once.
    """
    if query.min_size is None and query.max_size is None:
        return True
    if size is None:
        return False
    if query.min_size is not None and size < query.min_size:
        return False
    return query.max_size is None or size <= query.max_size


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
