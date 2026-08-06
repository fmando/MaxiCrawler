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

__all__ = [
    "ContentEncodingError",
    "ContentTypeError",
    "CrawlError",
    "CrawlResult",
    "FetchError",
    "FetchedPage",
    "HtmlDocument",
    "HttpStatusError",
    "LinkKind",
    "PageInfo",
    "PageLink",
    "ParsedHtml",
    "PolicyRefusedError",
    "RawLink",
    "ResponseTooLargeError",
    "TooManyRedirectsError",
    "TransportError",
    "UnsupportedSchemeError",
]
