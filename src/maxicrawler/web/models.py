"""Immutable value objects describing one crawled web page.

These models live outside :mod:`maxicrawler.domain` for the same reason
:class:`~maxicrawler.documents.Document` does: they are the vocabulary of one
infrastructure layer. The domain learns no HTTP.

Two models describe the retrieval rather than one. :class:`FetchedPage` carries
the response body and is short-lived — it exists between the fetcher and the
parser and is then dropped. :class:`PageInfo` is what survives into a
:class:`CrawlResult` and carries no body at all, so a crawl over ten thousand
pages cannot accumulate ten thousand response bodies.
"""

from dataclasses import dataclass
from enum import StrEnum

from maxicrawler.crawler import DiscoverySummary


class LinkKind(StrEnum):
    """What kind of reference a link was found as.

    The kind describes the *source* of the reference, not what it points at:
    the crawler never fetches a link, so it has no way of knowing whether an
    ``<img src>`` really is an image.
    """

    ANCHOR = "anchor"
    """``<a href>`` and ``<area href>`` — a navigable link."""

    IMAGE = "image"
    """``<img src>``."""

    SCRIPT = "script"
    """``<script src>``."""

    STYLESHEET = "stylesheet"
    """``<link href>``, whatever its ``rel``."""

    FRAME = "frame"
    """``<iframe src>``."""

    REDIRECT = "redirect"
    """``<meta http-equiv="refresh">`` — a navigation the page asks for.

    It is reported as a link rather than followed. The crawler was asked for
    one page and returns what that page says, including where it wants to send
    the reader next.
    """

    TEXT = "text"
    """A bare URL written in the page's prose rather than in markup.

    This is how a share link usually appears on a forum page, so it is found
    with the same rule :mod:`maxicrawler.extractors` applies to plain text and
    Markdown documents.
    """


@dataclass(frozen=True, slots=True)
class RawLink:
    """A link target exactly as the markup stated it.

    Produced by the parser, which knows nothing about URLs; turned into a
    :class:`PageLink` by :mod:`maxicrawler.web.resolve`.
    """

    value: str
    kind: LinkKind
    tag: str
    attribute: str


@dataclass(frozen=True, slots=True)
class PageLink:
    """A link found on a page, resolved to an absolute HTTP(S) URL."""

    raw_url: str
    """The target as written in the markup, before resolution."""

    resolved_url: str
    """The absolute HTTP(S) URL, resolved against the page's base URL."""

    kind: LinkKind
    tag: str
    attribute: str


@dataclass(frozen=True, slots=True)
class ParsedHtml:
    """What the markup of a page declares, before any URL is resolved.

    Deliberately free of URL knowledge, so the parser can be tested without a
    base URL and the resolver without any HTML.
    """

    base_href: str | None = None
    """The ``href`` of the first ``<base>`` element, unresolved."""

    title: str | None = None
    canonical_href: str | None = None
    """The ``href`` of ``<link rel="canonical">``, unresolved."""

    raw_links: tuple[RawLink, ...] = ()
    text: str = ""
    """The page's prose, with script and style content removed."""

    truncated: bool = False
    """Whether the link limit was reached and later links were dropped."""


@dataclass(frozen=True, slots=True)
class HtmlDocument:
    """A parsed page whose links have been resolved."""

    url: str
    """The page's own URL — the final one, after redirects."""

    base_url: str
    """What relative URLs were resolved against.

    The ``href`` of the first ``<base>`` element resolved against :attr:`url`,
    or :attr:`url` itself when the page declares no base.
    """

    encoding: str
    """The encoding the body was decoded as."""

    title: str | None = None
    canonical_url: str | None = None
    """``<link rel="canonical">`` resolved against the base URL.

    Recorded, never acted on. Treating it as identity is a de-duplication
    policy that belongs to a recursive crawl; applying it here would report
    links under a URL that was never fetched.
    """

    links: tuple[PageLink, ...] = ()
    """Every resolved link, in document order, duplicates kept.

    Duplicates are counted by :class:`~maxicrawler.crawler.DiscoveryPipeline`,
    which is what makes "42 links, 30 unique" reportable.
    """

    skipped_links: int = 0
    """Link targets that were dropped because they are not HTTP(S) URLs.

    ``mailto:``, ``javascript:``, ``tel:``, ``data:``, same-document ``#``
    references, and anything unparsable.
    """

    truncated: bool = False
    """Whether the parser's link limit was reached."""


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """One retrieved document, body included.

    Short-lived: it exists between :meth:`~maxicrawler.web.fetcher.PageFetcher.fetch`
    and the decoder. What outlives a crawl is :class:`PageInfo`.
    """

    requested_url: str
    """The URL we were asked for."""

    final_url: str
    """The URL that answered, after every redirect."""

    status: int
    body: bytes
    content_type: str | None = None
    """The media type, with its parameters removed."""

    declared_charset: str | None = None
    """The ``charset`` parameter of the ``Content-Type`` header, if any."""

    content_encoding: str | None = None
    """The ``Content-Encoding`` that was decompressed, if any."""

    redirects: tuple[str, ...] = ()
    """Every URL the chain passed through, in order, excluding the first."""

    @property
    def was_redirected(self) -> bool:
        """Return whether the answering URL differs from the requested one."""
        return bool(self.redirects)


@dataclass(frozen=True, slots=True)
class PageInfo:
    """What a retrieval reports once its body has been consumed."""

    requested_url: str
    """The URL we were asked for."""

    final_url: str
    """The URL that answered, after every redirect."""

    status: int
    size: int
    """The length of the decoded body in bytes, after decompression."""

    encoding: str
    content_type: str | None = None
    content_encoding: str | None = None
    redirects: tuple[str, ...] = ()

    @property
    def was_redirected(self) -> bool:
        """Return whether the answering URL differs from the requested one."""
        return bool(self.redirects)

    @classmethod
    def of(cls, page: FetchedPage, *, encoding: str, size: int) -> "PageInfo":
        """Return the body-free description of *page*."""
        return cls(
            requested_url=page.requested_url,
            final_url=page.final_url,
            status=page.status,
            size=size,
            encoding=encoding,
            content_type=page.content_type,
            content_encoding=page.content_encoding,
            redirects=page.redirects,
        )


@dataclass(frozen=True, slots=True)
class CrawlResult:
    """Everything one crawl of one page produced.

    The result composes :class:`~maxicrawler.crawler.DiscoverySummary` rather
    than restating its fields, so the counters, the plugin tally, and the
    renderer are the same ones the offline discovery workflow uses.

    Both URLs of the retrieval are kept: :attr:`requested_url` is what was
    asked for and is the identity a crawl queue, a history, and a user
    interface refer to; :attr:`final_url` is what answered and is what every
    relative link on the page was resolved against. A redirect makes the two
    differ, and losing either one later is expensive — so both are stated here
    rather than only inside :attr:`page`.
    """

    page: PageInfo
    document: HtmlDocument
    summary: DiscoverySummary

    @property
    def requested_url(self) -> str:
        """Return the URL the crawl was asked for."""
        return self.page.requested_url

    @property
    def final_url(self) -> str:
        """Return the URL that answered, after every redirect."""
        return self.page.final_url

    @property
    def was_redirected(self) -> bool:
        """Return whether the request was redirected."""
        return self.page.was_redirected

    @property
    def redirects(self) -> tuple[str, ...]:
        """Return every URL the redirect chain passed through."""
        return self.page.redirects

    @property
    def links(self) -> tuple[PageLink, ...]:
        """Return every resolved link the page contained."""
        return self.document.links

    @property
    def link_count(self) -> int:
        """Return how many links were found, duplicates included."""
        return len(self.document.links)

    @property
    def skipped_links(self) -> int:
        """Return how many link targets were not HTTP(S) URLs."""
        return self.document.skipped_links

    def links_by_kind(self) -> dict[LinkKind, int]:
        """Return how many links of each kind were found, in enum order."""
        counts = {kind: 0 for kind in LinkKind}
        for link in self.document.links:
            counts[link.kind] += 1
        return {kind: count for kind, count in counts.items() if count}
