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
service the queue already downloads through. And it was the seam an
*"already in the library"* filter was meant to go through unchanged. It now
does; see below.

**What is already known about a link is a set of states, not a flag.**
:class:`LinkState` is what a report says about a URL beyond what the crawl found:
that the library holds something fetched from it, that the queue is about to.
Each state is answered by one callable of the same shape as the downloadable
resolver, and they arrive as a mapping — so a state this release has not thought
of is an enum member, a resolver and a label, and no signature anywhere changes.

That generality is the point rather than a flourish. Two of the states already
on the roadmap do not fit a boolean: *the same content is here under a different
URL*, which SHA-256 answers, and *this file is reachable from another source
too*, which is one URL standing for several. Both are "what is known about this
link", both belong in the same row of the same table, and neither should cost a
migration of the vocabulary to add.

**A state is a claim about the URL, never about completeness.** A share that
names a folder is recorded by every file inside it — the entries carry the
container's URL (that is what makes the question answerable at all) — so
:attr:`LinkState.IN_LIBRARY` means *something* fetched from here is stored, not
that all of it is. The wording that reaches a person has to leave room for that,
which is why these are states with names rather than sentences with verbs.
"""

from collections.abc import Callable, Iterable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from enum import StrEnum
from math import ceil
from typing import Any

from maxicrawler.app.targets import TargetKind, target_of
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

UNTRACKED = "(new)"
"""What a URL in no state at all is called, as a facet and as a filter value.

The same shape as :data:`UNRESOLVED`, and parenthesised for the same reason: it
travels in a query string beside real state names, and a state added later must
not be able to collide with it.

It is a sentinel rather than a :class:`LinkState` member on purpose. "In none of
the states" is not a state — it is the absence of all of them, and it stays
correct when a fourth state is added, which an enum member spelled ``NEW`` would
quietly stop being.
"""


class LinkState(StrEnum):
    """Something known about a URL beyond the fact that a crawl found it.

    Deliberately open. Adding *the same content is already here under another
    URL*, or *this file is reachable from a second source*, means adding a member
    and a resolver — not changing how a report asks, filters, counts or renders.
    """

    IN_LIBRARY = "library"
    """Something fetched from this URL is stored.

    Not "this link has been downloaded", and the difference is real: a share
    naming a folder is recorded by each file inside it under the container's own
    URL, so one stored file is enough to put the folder link in this state.
    """

    IN_QUEUE = "queue"
    """This URL is waiting to be fetched, or is being fetched right now."""

    @classmethod
    def parse(cls, value: str | None) -> "LinkState | None":
        """Return the state *value* names, or ``None`` when it names none.

        Lenient like :meth:`LinkSort.parse`, and for the same reason: the value
        arrives in a query string, where a bookmark that predates a state being
        renamed is ordinary.
        """
        try:
            return cls(value or "")
        except ValueError:
            return None


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
    target: TargetKind
    """What the URL says it points at; see :mod:`maxicrawler.app.targets`.

    Computed once when the row is read rather than on demand, because filtering
    and counting both ask every item for it and the answer cannot change.
    """

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
    target: TargetKind | None = None
    """What the URL points at — the documents, the images, the archives."""

    downloadable: bool | None = None
    """``True`` for only what can be fetched, ``False`` for only what cannot."""

    state: str | None = None
    """A :class:`LinkState` value, or :data:`UNTRACKED` for what is in none.

    One value rather than a set, the way the plugin and category filters are one
    value: these arrive from a chip that carries its own count, and "how many of
    these are there" is most of what decides whether you want only those.
    """

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
            or self.target is not None
            or self.downloadable is not None
            or self.state is not None
            or self.normalized_only
        )


@dataclass(frozen=True, slots=True)
class Matches:
    """The fetchable URLs a query matched, and how many there were."""

    urls: tuple[str, ...]
    """As many as the caller had room for, in the order the report shows them."""

    total: int
    """How many matched, whether or not they fit."""

    @property
    def left_over(self) -> int:
        """Return how many matched but did not fit."""
        return max(0, self.total - len(self.urls))


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
    targets: tuple[LinkFacet, ...] = ()
    states: tuple[LinkFacet, ...] = ()
    """What is present in the whole crawl, whatever the query asked for.

    Counted over every recorded URL rather than over the matches, the same way
    the library lists its providers: choosing one filter must never remove the
    entry you would use to choose a different one.
    """

    downloadable: frozenset[str] = frozenset()
    """Which of :attr:`items` some provider here could fetch."""

    known: Mapping[LinkState, frozenset[str]] = field(default_factory=dict)
    """Which of :attr:`items` are in each state, for the states that were asked.

    A mapping rather than a member per state, so that rendering a row means
    walking what is there. A state nobody supplied a resolver for is absent
    rather than empty: *"we did not ask"* and *"we asked and the answer was
    none"* are different, and only one of them should put a column on a page.
    """

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

StateResolver = Callable[[Iterable[str]], AbstractSet[str]]
"""Answers which of some URLs are in one :class:`LinkState`.

The same shape as :data:`DownloadableResolver`, deliberately: one question, one
callable, asked in bulk, answered without contacting anything. Satisfied today by
:meth:`~maxicrawler.app.library.LibraryService.stored` and by the queue's own
membership, and by whatever answers *"is this the same content?"* later.

Returning the URLs *as they were asked* matters, and is the resolver's job
rather than this module's. A library records a share link without its fragment
while a report holds it with one, and only the side that knows why can strip it.
"""


class DiscoveryService:
    """Everything a client needs to read a crawl's findings, and nothing about showing them."""

    def __init__(
        self,
        settings: Settings,
        *,
        repository: SQLiteDiscoveryRepository | None = None,
        downloadable: DownloadableResolver | None = None,
        states: Mapping[LinkState, StateResolver] | None = None,
    ) -> None:
        self._settings = settings
        self._injected_repository = repository
        self._cached_repository: SQLiteDiscoveryRepository | None = None
        self._downloadable = downloadable
        self._states: Mapping[LinkState, StateResolver] = dict(states or {})

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
        # Over every recorded URL rather than over the matches, because the same
        # answer serves three purposes: the filter, the counts on the chips, and
        # the marks on the rows. Resolving it once is also what keeps the cost one
        # question per state instead of one per state per use.
        known = self._known(recorded)
        matching = tuple(item for item in recorded if _matches(item, asked, known))
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
            targets=_target_facets(recorded),
            states=_state_facets(recorded, known),
            known={
                state: frozenset(item.url for item in shown if item.url in urls)
                for state, urls in known.items()
            },
            # Already known when the filter forced the whole candidate set to be
            # resolved; asked only about this page when it did not.
            downloadable=(
                frozenset(item.url for item in shown if item.url in fetchable)
                if fetchable is not None
                else self._resolve(shown)
            ),
        )

    def fetchable(self, session_id: str, query: LinkQuery | None = None, *, limit: int) -> Matches:
        """Return the URLs *query* matches that some provider here could fetch.

        A different question from :meth:`browse`, and worth its own method
        rather than a page size nobody would want to render: this one is asked
        by *"queue everything I am looking at"*, where the answer is a set of
        URLs and nothing about tables, ordering or facets.

        Ordered the way the report orders them, so what comes back first is
        what was at the top of the list somebody was reading. Cut at *limit*,
        with the count of what did not fit — the caller has a queue with a
        ceiling, and silently dropping the remainder would be the one outcome
        it must not have.

        The URLs keep their fragments, which is what makes this the safer half
        of the feature: a share link's decryption key never has to travel to a
        browser and back to be queued.
        """
        if limit < 0:
            msg = "limit cannot be negative"
            raise ValueError(msg)
        asked = query if query is not None else LinkQuery()
        # A filter asking for what *cannot* be fetched matches nothing that can,
        # by definition. Answered here rather than by resolving a set only to
        # discard all of it.
        if asked.downloadable is False:
            return Matches(urls=(), total=0)
        recorded = self.links(session_id)
        # Resolved once, above the loop that reads it. Asking inside the
        # comprehension is one full pass over the library *per link*, which is
        # the shape of question `stored` exists to be spared: a report of ten
        # thousand made it a walk over ten thousand libraries, on the event loop,
        # for one click. Same reason `browse` resolves it once; see `_known`.
        known = self._known(recorded, asked)
        matching = tuple(item for item in recorded if _matches(item, asked, known))
        fetchable = self._resolve(matching)
        wanted = tuple(item.url for item in _ordered(matching, asked) if item.url in fetchable)
        return Matches(urls=wanted[:limit], total=len(wanted))

    def _resolve(self, items: Iterable[LinkItem]) -> frozenset[str]:
        """Return which of *items* some provider here could fetch."""
        if self._downloadable is None:
            return frozenset()
        return frozenset(self._downloadable(item.url for item in items))

    def _known(
        self, items: Iterable[LinkItem], query: LinkQuery | None = None
    ) -> dict[LinkState, frozenset[str]]:
        """Return which of *items* are in each state worth asking about.

        Every configured state when *query* is absent, because a report counts
        them all whether or not it filters by one. Only what the filter needs
        when *query* is given: *"queue every match"* has no chips to fill in, and
        asking the library about a state nobody filtered by would be one full
        question per click for an answer nothing reads.

        A state whose resolver is not configured is absent from the result rather
        than empty — see :attr:`LinkPage.known`.
        """
        wanted = self._wanted(query)
        if not wanted:
            return {}
        urls = tuple(item.url for item in items)
        return {state: frozenset(self._states[state](urls)) for state in wanted}

    def _wanted(self, query: LinkQuery | None) -> tuple[LinkState, ...]:
        """Return the states that have to be resolved to answer *query*."""
        if query is None or query.state == UNTRACKED:
            # "In none of them" is only answerable by asking all of them.
            return tuple(self._states)
        asked = LinkState.parse(query.state)
        return (asked,) if asked is not None and asked in self._states else ()

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
        target=target_of(record.normalized_url),
        position=position,
    )


def _matches(item: LinkItem, query: LinkQuery, known: Mapping[LinkState, frozenset[str]]) -> bool:
    """Return whether *item* belongs in the answer to *query*.

    Everything except downloadability, which needs the whole candidate set at
    once and is applied by the caller.

    *known* is the same resolution the page is built from, passed in rather than
    asked for here: it costs a question per state, and a predicate called once
    per recorded URL is the last place that should be asking one.
    """
    if not _matches_state(item, query.state, known):
        return False
    if query.plugin is not None and item.facet != query.plugin:
        return False
    if query.category is not None and (item.category or "") != query.category:
        return False
    if query.target is not None and item.target is not query.target:
        return False
    if query.normalized_only and not item.was_normalized:
        return False
    if not query.search:
        return True
    needle = query.search.casefold()
    haystack = (item.url, item.raw_url, item.source_url or "")
    return any(needle in value.casefold() for value in haystack)


def _matches_state(
    item: LinkItem, wanted: str | None, known: Mapping[LinkState, frozenset[str]]
) -> bool:
    """Return whether *item* is in the state *wanted* names.

    A filter naming a state nothing answered matches everything rather than
    nothing. The alternative is an empty table for a bookmark that predates a
    resolver being configured, which reads as *"this crawl found nothing"* — the
    one thing a report must never say when it is not true.
    """
    if wanted is None:
        return True
    if wanted == UNTRACKED:
        return not any(item.url in urls for urls in known.values())
    state = LinkState.parse(wanted)
    if state is None or state not in known:
        return True
    return item.url in known[state]


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


def _state_facets(
    items: Iterable[LinkItem], known: Mapping[LinkState, frozenset[str]]
) -> tuple[LinkFacet, ...]:
    """Return what is known about these URLs, the ones not yet known first.

    Nothing at all when no state was resolved. A row of chips claiming every URL
    is new would be a claim this installation did not make: *"nobody asked"* and
    *"the answer was none"* look identical once counted, and only one of them is
    something to put on a page.

    :data:`UNTRACKED` leads because it is the one people reach for — a second
    crawl of a site is run to find what the first one did not have. The states
    themselves follow in the order they are declared rather than by count, for
    the reason :func:`_target_facets` gives.

    A state nothing is in is left out, the same way an absent target kind is. A
    filter that can only produce an empty table is not worth a chip.
    """
    if not known:
        return ()
    urls = tuple(item.url for item in items)
    counts = {
        state: sum(1 for url in urls if url in matched)
        for state, matched in known.items()
        if state in LinkState
    }
    untracked = sum(1 for url in urls if not any(url in matched for matched in known.values()))
    facets = [LinkFacet(value=UNTRACKED, count=untracked)] if untracked else []
    facets.extend(
        LinkFacet(value=str(state), count=counts[state]) for state in LinkState if counts.get(state)
    )
    return tuple(facets)


def _target_facets(items: Iterable[LinkItem]) -> tuple[LinkFacet, ...]:
    """Return what these URLs point at, in the order the kinds are declared.

    Enum order rather than by count, the way a crawl report already lists its
    link kinds. Frequency would put "unknown" first on every crawl of every
    site, because most URLs name no suffix — and a list whose first entry is
    always the same word is not a list anybody reads.

    Kinds that are not present are left out. A filter for "no images here" can
    only disappoint.
    """
    counts: dict[TargetKind, int] = {}
    for item in items:
        counts[item.target] = counts.get(item.target, 0) + 1
    return tuple(
        LinkFacet(value=str(kind), count=counts[kind]) for kind in TargetKind if counts.get(kind)
    )
