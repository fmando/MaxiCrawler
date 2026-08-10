"""Application services shared by every client.

This package is the **composition root**. It is the one place allowed to know
about `config`, `database`, `web`, `crawler`, `providers`, `downloader` and
`library` at once, and to wire them into something a client can call.

That is why it exists rather than living in :mod:`maxicrawler.web`: the crawler
must not import `database`, and a composition root must import both. Until now
the command line was that root, which was fine while it was the only client.

Four services, one question each:

* :class:`CrawlService` — *"what does this site link to?"*
* :class:`DiscoveryService` — *"what did that crawl find?"*
* :class:`DownloadService` — *"fetch this link into the library."*
* :class:`LibraryService` — *"what is in the library?"*

The first two are a writer and a reader of the same records, kept apart for the
reason ADR-028 keeps downloading and browsing apart: neither should end up
speaking the other's vocabulary.

Everything here is free of any interface. No terminal, no HTTP, no printing,
no exit codes. A client decides how to render what it gets back, and the two
clients that exist — the CLI and the web interface — share every decision that
comes before that.
"""

from maxicrawler.app.crawling import CrawlService
from maxicrawler.app.discovery import (
    DEFAULT_LINKS_PER_PAGE,
    MAX_LINKS_PER_PAGE,
    UNRESOLVED,
    DiscoveryService,
    LinkFacet,
    LinkItem,
    LinkPage,
    LinkQuery,
    LinkSort,
)
from maxicrawler.app.downloading import (
    DownloadControl,
    DownloadProgress,
    DownloadService,
    DownloadSummary,
    ProgressListener,
)
from maxicrawler.app.library import (
    DEFAULT_PER_PAGE,
    MAX_PER_PAGE,
    LibraryItem,
    LibraryPage,
    LibraryQuery,
    LibraryService,
    LibrarySort,
    StoredPayload,
)
from maxicrawler.app.serialization import crawl_document, page_document
from maxicrawler.app.viewing import DEFAULT_MAX_VIEW_BYTES, Display, MediaVerdict, verdict_for

__all__ = [
    "DEFAULT_LINKS_PER_PAGE",
    "DEFAULT_MAX_VIEW_BYTES",
    "DEFAULT_PER_PAGE",
    "MAX_LINKS_PER_PAGE",
    "MAX_PER_PAGE",
    "UNRESOLVED",
    "CrawlService",
    "DiscoveryService",
    "Display",
    "DownloadControl",
    "DownloadProgress",
    "DownloadService",
    "DownloadSummary",
    "LibraryItem",
    "LibraryPage",
    "LibraryQuery",
    "LibraryService",
    "LibrarySort",
    "LinkFacet",
    "LinkItem",
    "LinkPage",
    "LinkQuery",
    "LinkSort",
    "MediaVerdict",
    "ProgressListener",
    "StoredPayload",
    "crawl_document",
    "page_document",
    "verdict_for",
]
