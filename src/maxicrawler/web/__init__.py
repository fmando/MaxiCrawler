"""Retrieval of web pages and discovery of the URLs they contain.

This layer answers exactly one question: *"which URLs does this page
contain?"*. It fetches a document over HTTP, decodes it, parses its markup,
resolves every reference to an absolute URL, and hands the result to the
existing discovery pipeline.

It knows nothing about providers, downloads, or the library, and it must stay
that way — a URL found here is classified by the same plugins that classify a
URL found in a local document, and nothing more happens to it.

The layer fetches exactly one page per call and holds no state about which page
to visit next, so recursion is a matter of who calls it in what order rather
than a change inside it.
"""

from maxicrawler.web.errors import (
    ContentEncodingError,
    ContentTypeError,
    CrawlError,
    FetchError,
    HttpStatusError,
    PolicyRefusedError,
    ResponseTooLargeError,
    TooManyRedirectsError,
    TransportError,
    UnsupportedSchemeError,
)
from maxicrawler.web.fetcher import PageFetcher, UrllibPageFetcher
from maxicrawler.web.models import (
    CrawlResult,
    FetchedPage,
    HtmlDocument,
    LinkKind,
    PageInfo,
    PageLink,
    ParsedHtml,
    RawLink,
)
from maxicrawler.web.parser import HtmlLinkParser, HtmlParser
from maxicrawler.web.policy import AllowAllPolicy, CrawlPolicy, PolicyDecision, PolicyRule
from maxicrawler.web.service import WebDiscoveryService

__all__ = [
    "AllowAllPolicy",
    "ContentEncodingError",
    "ContentTypeError",
    "CrawlError",
    "CrawlPolicy",
    "CrawlResult",
    "FetchError",
    "FetchedPage",
    "HtmlDocument",
    "HtmlLinkParser",
    "HtmlParser",
    "HttpStatusError",
    "LinkKind",
    "PageFetcher",
    "PageInfo",
    "PageLink",
    "ParsedHtml",
    "PolicyDecision",
    "PolicyRefusedError",
    "PolicyRule",
    "RawLink",
    "ResponseTooLargeError",
    "TooManyRedirectsError",
    "TransportError",
    "UnsupportedSchemeError",
    "UrllibPageFetcher",
    "WebDiscoveryService",
]
