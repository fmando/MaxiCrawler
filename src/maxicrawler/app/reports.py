"""Reading the pages one crawl reached.

The other half of the same question :mod:`maxicrawler.app.discovery` answers,
and deliberately not in that module: a discovered URL is a row in a database,
while a page outcome exists only in the report the process that ran the crawl
is holding. Per-page persistence is on the roadmap and would turn these
functions into methods on a service; until then there is nothing to open, so
this is a pure query over a value somebody already has.

That is also why it is worth writing now rather than after. What a client asks
for — *"only the failures"*, *"only the ones that redirected"* — is a decision
about which records to show, and it does not become presentation just because
the records happen to be in memory. Keeping the vocabulary here means the day
those outcomes get a table, the callers do not change.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import ceil

from maxicrawler.web.report import PageOutcome

DEFAULT_PAGES_PER_PAGE = 100
"""How many crawled pages one page of the report's page table shows."""

MAX_PAGES_PER_PAGE = 1000
"""Ceiling on what a caller may ask for in one page."""


class PageState(StrEnum):
    """Which pages of a crawl a caller wants to see.

    One selector rather than two, and that is a simplification worth naming:
    succeeded and failed are opposite answers to one question, while redirected
    is a separate fact that either of them can carry. Asking for "the failures
    that also redirected" is therefore not expressible here — and is a question
    nobody has asked, while "show me the failures" is the first thing anybody
    asks of a crawl of four hundred pages.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REDIRECTED = "redirected"
    """Answered by a URL other than the one requested, however it then went."""

    @classmethod
    def parse(cls, value: str | None) -> "PageState | None":
        """Return the state *value* names, or ``None`` for everything else.

        ``None`` means "no filter" and is also what an unrecognised value gets:
        this arrives in a query string, where a stale bookmark is ordinary, and
        the whole table beats a refusal.
        """
        try:
            return cls(value or "")
        except ValueError:
            return None

    def matches(self, page: PageOutcome) -> bool:
        """Return whether *page* belongs in the answer to this state."""
        match self:
            case PageState.SUCCEEDED:
                return page.succeeded
            case PageState.FAILED:
                return not page.succeeded
            case _:
                return page.was_redirected


@dataclass(frozen=True, slots=True)
class PageQuery:
    """What a caller wants to see of the pages a crawl reached."""

    search: str = ""
    """Matched against the requested URL, the one that answered, and the title."""

    state: PageState | None = None
    page: int = 1
    per_page: int = DEFAULT_PAGES_PER_PAGE

    @property
    def is_filtered(self) -> bool:
        """Return whether this query shows less than every page."""
        return bool(self.search) or self.state is not None


@dataclass(frozen=True, slots=True)
class PageCounts:
    """How many pages of a crawl fall into each state.

    Counted over every page rather than over the matches, the same way the link
    table counts its facets: choosing a filter must not remove the entry you
    would use to choose a different one.
    """

    succeeded: int = 0
    failed: int = 0
    redirected: int = 0

    def of(self, state: PageState) -> int:
        """Return the count for *state*."""
        match state:
            case PageState.SUCCEEDED:
                return self.succeeded
            case PageState.FAILED:
                return self.failed
            case _:
                return self.redirected


@dataclass(frozen=True, slots=True)
class PageSlice:
    """One page of the page table, and enough about the rest to navigate it."""

    items: tuple[PageOutcome, ...]
    query: PageQuery
    total: int
    """How many pages matched the query."""

    recorded: int
    """How many the crawl reached altogether, matched or not."""

    page: int
    pages: int
    counts: PageCounts = PageCounts()

    @property
    def hidden(self) -> int:
        """Return how many matched pages are not on this page."""
        return max(0, self.total - len(self.items))

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


def browse_pages(pages: Sequence[PageOutcome], query: PageQuery | None = None) -> PageSlice:
    """Return the slice of *pages* that *query* asks for.

    Filtered, then cut to a page — and never reordered. The order a crawl
    reached its pages in is information: it is how a reader sees that the
    failures all arrived after the twentieth page, which no sort would show.
    """
    asked = query if query is not None else PageQuery()
    matching = tuple(page for page in pages if _matches(page, asked))
    per_page = min(max(asked.per_page, 1), MAX_PAGES_PER_PAGE)
    total_pages = max(1, ceil(len(matching) / per_page))
    number = min(max(asked.page, 1), total_pages)
    start = (number - 1) * per_page
    return PageSlice(
        items=matching[start : start + per_page],
        query=asked,
        total=len(matching),
        recorded=len(pages),
        page=number,
        pages=total_pages,
        counts=count_pages(pages),
    )


def count_pages(pages: Sequence[PageOutcome]) -> PageCounts:
    """Return how many of *pages* fall into each state."""
    return PageCounts(
        succeeded=sum(1 for page in pages if page.succeeded),
        failed=sum(1 for page in pages if not page.succeeded),
        redirected=sum(1 for page in pages if page.was_redirected),
    )


def _matches(page: PageOutcome, query: PageQuery) -> bool:
    """Return whether *page* belongs in the answer to *query*."""
    if query.state is not None and not query.state.matches(page):
        return False
    if not query.search:
        return True
    needle = query.search.casefold()
    haystack = (page.url, page.final_url or "", page.title or "")
    return any(needle in value.casefold() for value in haystack)
