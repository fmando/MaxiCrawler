"""Reading what a crawl discovered, for whoever is asking.

:class:`~maxicrawler.app.crawling.CrawlService` writes discovery; this service
reads it. That is the same split ADR-028 made for the library, and it is made
here for the same reason: a report of a few thousand URLs is not a table to
print, it is a question to ask — *"which of these did the mega plugin claim?"*,
*"which of them could this installation actually fetch?"* — and a service that
both writes and answers questions ends up with one vocabulary doing two jobs.

Three properties are worth stating before the code.

**A report is a query.** :class:`LinkQuery` in, :class:`LinkPage` out. Searching,
filtering, ordering and paging all happen here, so the browser carries the
question in its URL and the template decides nothing. A future ``library
links``-style command would ask the same way and could not disagree about what
"sorted by plugin" means.

**Filtering happens in Python, not in SQL.** The same trade
:class:`~maxicrawler.app.library.LibraryService` makes, and the reason is the
same shape: one of the filters — *can this installation download it?* — is not a
column. It comes from a plugin classifying a URL string and a provider declaring
a capability, so a query that pushed the other predicates into SQL would still
have to read every candidate row to apply that one. What bounds the cost is the
crawl's own page ceiling: a run that may fetch a thousand pages cannot record an
unbounded number of URLs.

**Whether a link can be downloaded is asked from outside.** The resolver arrives
as a callable rather than a :class:`~maxicrawler.app.downloading.DownloadService`
this module constructs, for two reasons. It keeps the provider registry a
composition-root decision — the one that is built once and cached lives in the
service the queue already downloads through. And it is the seam a later
*"already in the library"* filter goes through unchanged: that answer comes from
:class:`~maxicrawler.app.library.LibraryService`, has exactly this shape, and
should not require this module to learn a third collaborator.
"""

from collections.abc import Callable, Iterable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from enum import StrEnum
from math import ceil
from typing import Any

from maxicrawler.config import Settings
from maxicrawler.database import SQLiteDatabase, SQLiteDiscoveryRepository, StoredUrl
from maxicrawler.plugins.generic import GENERIC_PLUGIN_NAME

DEFAULT_LINKS_PER_PAGE = 200
"""How many discovered URLs one page of a report shows.

Larger than the library's fifty because these rows are one line each and are
read by scanning rather than by comparing. Small enough that a browser still
lays the table out instantly.
"""

MAX_LINKS_PER_PAGE = 1000
"""Ceiling on what a caller may ask for in one page.

A request for every row of a large crawl is either a mistake or an attempt to
make the server render for a minute. The JSON document is the answer for anyone
who genuinely wants all of them.
"""

UNRESOLVED = "(unresolved)"
"""What a URL no plugin claimed is called, as a facet and as a filter value.

The parentheses are not decoration: this value travels in a query string beside
real plugin names, and a plugin is free to call itself anything. A name no
plugin registry would accept is what keeps the sentinel from being shadowed.
"""


class LinkSort(StrEnum):
    """Which order a report lists discovered URLs in."""

    RELEVANCE = "relevance"
    """Host-specific plugins first, then the generic fallback, then unresolved.

    The default, and not the honest discovery order, deliberately: a page full
    of share links produces thousands of generic URLs and a handful of Mega
    ones, and a first page ordered by discovery would contain none of the links
    this project exists to find. Discovery order is kept *within* each group,
    and is available on its own as :attr:`DISCOVERED`.
    """

    DISCOVERED = "discovered"
    """The order the crawl found them."""

    URL = "url"
    PLUGIN = "plugin"
    SOURCE = "source"
    """The page a URL was found on, which groups a report by where it came from."""

    @classmethod
    def parse(cls, value: str | None, *, default: "LinkSort") -> "LinkSort":
        """Return the sort *value* names, or *default* when it names none.

        Lenient for the same reason
        :meth:`~maxicrawler.app.library.LibrarySort.parse` is: the value arrives
        in a query string, where a stale bookmark is ordinary, and a report in
        the wrong order beats a refusal.
        """
        try:
            return cls(value or "")
        except ValueError:
            return default


@dataclass(frozen=True, slots=True)
class LinkItem:
    """One discovered URL, as a client lists it.

    ``plugin`` and ``category`` stay ``None`` when nothing claimed the URL. What
    to *call* that is a wording decision and lives in the layer that renders it;
    this one only knows that nobody answered.
    """

    url: str
    """The normalized URL, which is what everything else addresses it by."""

    raw_url: str
    source_url: str | None
    plugin: str | None
    category: str | None
    position: int
    """Where in the crawl's discovery order this URL arrived, counting from zero.

    Kept so every ordering can fall back to it. Two URLs that compare equal on
    the column being sorted then never swap places between two requests, which
    is what makes paging stable.
    """

    @property
    def was_normalized(self) -> bool:
        """Return whether the URL differs from the way it was written."""
        return self.raw_url != self.url

    @property
    def priority(self) -> int:
        """Return which group this sorts into: host, generic, unresolved."""
        if self.plugin is None:
            return 2
        return 1 if self.plugin == GENERIC_PLUGIN_NAME else 0

    @property
    def is_notable(self) -> bool:
        """Return whether a host-specific plugin claimed this URL."""
        return self.priority == 0

    @property
    def facet(self) -> str:
        """Return the plugin name a filter addresses this URL by."""
        return UNRESOLVED if self.plugin is None else self.plugin


@dataclass(frozen=True, slots=True)
class LinkFacet:
    """One value a report can be filtered by, and how much of it there is."""

    value: str
    count: int


@dataclass(frozen=True, slots=True)
class LinkQuery:
    """What a caller wants to see of one crawl's discovered URLs."""

    search: str = ""
    """Matched against the normalized URL, the raw URL and the page it was on."""

    plugin: str | None = None
    """A plugin name, or :data:`UNRESOLVED` for the URLs nothing claimed."""

    category: str | None = None
    downloadable: bool | None = None
    """``True`` for only what can be fetched, ``False`` for only what cannot."""

    normalized_only: bool = False
    """Only URLs that differ from the way they were written.

    The nearest honest answer to "show me the duplicates". Duplicates are
    removed by the discovery pipeline and survive only as a counter, and the
    stored rows are unique per crawl by construction — so there is no duplicate
    row to filter for. What there is, and what is worth seeing, is the URLs
    normalization changed.
    """

    sort: LinkSort = LinkSort.RELEVANCE
    descending: bool = False
    page: int = 1
    per_page: int = DEFAULT_LINKS_PER_PAGE

    @property
    def is_filtered(self) -> bool:
        """Return whether this query shows less than the whole crawl."""
        return bool(self.search) or (
            self.plugin is not None
            or self.category is not None
            or self.downloadable is not None
            or self.normalized_only
        )


@dataclass(frozen=True, slots=True)
class LinkPage:
    """One page of a report, and enough about the rest to navigate it."""

    items: tuple[LinkItem, ...]
    query: LinkQuery
    total: int
    """How many URLs matched the query."""

    recorded: int
    """How many the database holds for this crawl, matched or not."""

    discovered: int
    """How many the crawl itself counted.

    Not always what :attr:`recorded` says: a crawl run without persistence
    records nothing at all. Keeping the two apart is what lets a page say "not
    recorded" instead of showing an empty table that reads as "nothing found".
    """

    page: int
    pages: int
    plugins: tuple[LinkFacet, ...] = ()
    categories: tuple[LinkFacet, ...] = ()
    """What is present in the whole crawl, whatever the query asked for.

    Counted over every recorded URL rather than over the matches, the same way
    the library lists its providers: choosing one filter must never remove the
    entry you would use to choose a different one.
    """

    downloadable: frozenset[str] = frozenset()
    """Which of :attr:`items` some provider here could fetch."""

    @property
    def hidden(self) -> int:
        """Return how many matched URLs are not on this page."""
        return max(0, self.total - len(self.items))

    @property
    def was_recorded(self) -> bool:
        """Return whether this crawl's URLs were written down at all."""
        return self.recorded > 0 or self.discovered == 0

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


DownloadableResolver = Callable[[Iterable[str]], AbstractSet[str]]
"""Answers which of some URLs can be fetched, without contacting anything.

Satisfied by :meth:`~maxicrawler.app.downloading.DownloadService.downloadable`
without having been written for it. Asked in bulk rather than per URL because
the answer costs a plugin resolution and a registry lookup each, and a report
would otherwise ask it a thousand times over.
"""


class DiscoveryService:
    """Everything a client needs to read a crawl's findings, and nothing about showing them."""

    def __init__(
        self,
        settings: Settings,
        *,
        repository: SQLiteDiscoveryRepository | None = None,
        downloadable: DownloadableResolver | None = None,
    ) -> None:
        self._settings = settings
        self._injected_repository = repository
        self._cached_repository: SQLiteDiscoveryRepository | None = None
        self._downloadable = downloadable

    @property
    def settings(self) -> Settings:
        """Return the settings this service reads its database from."""
        return self._settings

    def links(self, session_id: str) -> tuple[LinkItem, ...]:
        """Return every URL one crawl recorded, in the order it found them.

        Empty for a crawl run without persistence, which is not the same thing
        as a crawl that found nothing — :attr:`LinkPage.was_recorded` is what
        tells the two apart, and a caller showing this should.
        """
        return tuple(
            _item(stored, position)
            for position, stored in enumerate(self._repository().stored_urls(session_id))
        )

    def browse(
        self, session_id: str, query: LinkQuery | None = None, *, discovered: int = 0
    ) -> LinkPage:
        """Return the page of one crawl's URLs *query* asks for.

        Filtered, then ordered, then cut to a page — in that order, because any
        other one answers a different question. Ordering after paging would sort
        a page rather than a crawl.

        *discovered* is what the crawl itself counted, which the database cannot
        know; see :attr:`LinkPage.discovered`.
        """
        asked = query if query is not None else LinkQuery()
        recorded = self.links(session_id)
        matching = tuple(item for item in recorded if _matches(item, asked))
        fetchable = self._resolve(matching) if asked.downloadable is not None else None
        if fetchable is not None:
            matching = tuple(
                item for item in matching if (item.url in fetchable) is asked.downloadable
            )
        ordered = _ordered(matching, asked)
        per_page = min(max(asked.per_page, 1), MAX_LINKS_PER_PAGE)
        pages = max(1, ceil(len(ordered) / per_page))
        page = min(max(asked.page, 1), pages)
        start = (page - 1) * per_page
        shown = ordered[start : start + per_page]
        return LinkPage(
            items=shown,
            query=asked,
            total=len(ordered),
            recorded=len(recorded),
            discovered=discovered,
            page=page,
            pages=pages,
            plugins=_plugin_facets(recorded),
            categories=_category_facets(recorded),
            # Already known when the filter forced the whole candidate set to be
            # resolved; asked only about this page when it did not.
            downloadable=(
                frozenset(item.url for item in shown if item.url in fetchable)
                if fetchable is not None
                else self._resolve(shown)
            ),
        )

    def _resolve(self, items: Iterable[LinkItem]) -> frozenset[str]:
        """Return which of *items* some provider here could fetch."""
        if self._downloadable is None:
            return frozenset()
        return frozenset(self._downloadable(item.url for item in items))

    def _repository(self) -> SQLiteDiscoveryRepository:
        """Return where discovered URLs are read from, creating the tables once.

        Cached rather than built per call. The repository itself opens a
        short-lived connection per operation, so what is saved is the
        ``CREATE TABLE IF NOT EXISTS`` on every request rather than a connection.
        """
        if self._injected_repository is not None:
            return self._injected_repository
        if self._cached_repository is None:
            repository = SQLiteDiscoveryRepository(SQLiteDatabase(self._settings.database_path))
            repository.initialize()
            self._cached_repository = repository
        return self._cached_repository


def _item(stored: StoredUrl, position: int) -> LinkItem:
    """Return one recorded URL as a listed item."""
    record = stored.record
    return LinkItem(
        url=record.normalized_url,
        raw_url=record.raw_url,
        source_url=record.source_url,
        plugin=stored.plugin_name,
        category=stored.category,
        position=position,
    )


def _matches(item: LinkItem, query: LinkQuery) -> bool:
    """Return whether *item* belongs in the answer to *query*.

    Everything except downloadability, which needs the whole candidate set at
    once and is applied by the caller.
    """
    if query.plugin is not None and item.facet != query.plugin:
        return False
    if query.category is not None and (item.category or "") != query.category:
        return False
    if query.normalized_only and not item.was_normalized:
        return False
    if not query.search:
        return True
    needle = query.search.casefold()
    haystack = (item.url, item.raw_url, item.source_url or "")
    return any(needle in value.casefold() for value in haystack)


def _ordered(items: tuple[LinkItem, ...], query: LinkQuery) -> tuple[LinkItem, ...]:
    """Return *items* in the order *query* asks for."""
    return tuple(sorted(items, key=lambda item: _sort_key(item, query), reverse=query.descending))


def _sort_key(item: LinkItem, query: LinkQuery) -> tuple[Any, ...]:
    """Return what one URL sorts by.

    Every ordering ends in the discovery position, so a page is stable between
    two requests. A value nobody recorded sorts last whichever way the order
    runs: a URL found on no page is not an early one, and a descending sort
    reversing it to the top would read as though it were.
    """
    absent = _absent(query.descending)
    match query.sort:
        case LinkSort.DISCOVERED:
            primary: tuple[Any, ...] = ()
        case LinkSort.URL:
            primary = (item.url.casefold(),)
        case LinkSort.PLUGIN:
            primary = (absent(item.plugin is None), (item.plugin or "").casefold())
        case LinkSort.SOURCE:
            primary = (absent(item.source_url is None), (item.source_url or "").casefold())
        case _:
            primary = (item.priority,)
    return (*primary, item.position)


def _absent(descending: bool) -> Callable[[bool], int]:
    """Return the ranking that keeps an unrecorded value last in either direction."""
    return lambda is_absent: int(is_absent) if not descending else int(not is_absent)


def _plugin_facets(items: Iterable[LinkItem]) -> tuple[LinkFacet, ...]:
    """Return which plugins claimed how much, the interesting ones first.

    The same order :func:`~maxicrawler.api.views.plugin_shares` uses and for the
    same reason: on a page full of share links the generic fallback always wins
    by volume, and a chip row that buried "mega 1,291" underneath it would hide
    the one number the crawl was run for.
    """
    counts: dict[str, int] = {}
    priorities: dict[str, int] = {}
    for item in items:
        counts[item.facet] = counts.get(item.facet, 0) + 1
        priorities[item.facet] = item.priority
    ordered = sorted(counts.items(), key=lambda entry: (priorities[entry[0]], -entry[1], entry[0]))
    return tuple(LinkFacet(value=value, count=count) for value, count in ordered)


def _category_facets(items: Iterable[LinkItem]) -> tuple[LinkFacet, ...]:
    """Return which categories are present, the most frequent first.

    URLs no plugin gave a category are counted under nothing rather than under
    an invented name: a filter entry that matched them would be a filter for
    "everything that is not classified", which the plugin facet already says
    better.
    """
    counts: dict[str, int] = {}
    for item in items:
        if item.category:
            counts[item.category] = counts.get(item.category, 0) + 1
    ordered = sorted(counts.items(), key=lambda entry: (-entry[1], entry[0]))
    return tuple(LinkFacet(value=value, count=count) for value, count in ordered)
