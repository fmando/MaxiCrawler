"""Application services shared by every client.

This package is the **composition root**. It is the one place allowed to know
about `config`, `database`, `web` and `crawler` at once, and to wire them into
something a client can call.

That is why it exists rather than living in :mod:`maxicrawler.web`: the crawler
must not import `database`, and a composition root must import both. Until now
the command line was that root, which was fine while it was the only client.

Everything here is free of any interface. No terminal, no HTTP, no printing,
no exit codes. A client decides how to render what it gets back, and the two
clients that exist — the CLI and the web interface — share every decision that
comes before that.
"""

from maxicrawler.app.crawling import CrawlService

__all__ = ["CrawlService"]
