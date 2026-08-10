# MaxiCrawler Roadmap

## Vision

Build a modular, plugin-driven link discovery and download platform.

The mission, core principles, and non-goals are described in
[VISION.md](VISION.md).

## Milestones

-   0.1 Foundation ✅
-   0.2 Domain Model & Discovery ✅
-   0.3 Plugin API ✅
-   0.4 Discovery Engine ✅
-   0.5 HTTP Engine ✅ — transport, retries, metadata inspection, and
    streaming transfers
-   0.6 First Host Plugins ✅ — Mega is classified by a plugin, inspected and
    downloaded by a provider
-   0.7 Download Manager & Library ✅ — provider-independent downloads into a
    long-lived, self-describing store
-   0.8 Web Discovery ✅ — one page is fetched, parsed, and fed to the existing
    discovery pipeline
-   0.9 Recursive Crawling ✅ — a frontier, a visited set and a crawl engine
    above the single-page crawler
-   0.10 Web interface ✅ — a browser client of the same services the command
    line uses, served by `maxicrawler serve`
-   0.11 The first end-to-end workflow ✅ — crawl, report, download, library,
    in a browser, through a `DownloadService` both clients share
-   0.12 Library comfort & document viewer ✅ — search, filter, sort, paging, a
    page per file, and the browser showing what it can
-   0.13 Politeness & robots.txt ✅ — robots.txt obeyed by default, per-host
    waiting, a private-network guard, and a stoppable download
-   0.14 Scheduler & Automation
-   0.15 REST API
-   1.0 Stable Release

The desktop GUI that used to sit at 0.11 is superseded by 0.10. A local server
and a browser reach every machine this runs on, including the ones it is run on
over SSH, and one interface that is maintained beats two that are not. The
`gui` package stays an empty placeholder rather than a promise.

## The chain

```text
Website → Crawler → Discovery → Plugin → Provider → Download Manager → Library
```

Each station answers exactly one question:

-   **Crawl Engine** — *"Which page comes next?"* Owns the frontier, the
    visited set and the limits; fetches nothing itself.
-   **Crawler** — *"Which URLs does this page contain?"* Fetches one page over
    HTTP; knows no provider, no download, and no library.
-   **Discovery** — *"Which URLs exist?"* Normalizes, deduplicates, and
    classifies whatever it is given, from a local document or from a page.
-   **Plugin** — *"Can I classify this URL?"* Pure, offline, runs on every
    discovered URL.
-   **Provider** — *"What can I do with this resource?"* May contact the host;
    invoked only by commands that perform network access.
-   **Download Manager** — *"How are downloads executed?"* Knows no provider.
-   **Library** — *"How are resources stored and managed?"* Knows no provider
    beyond its name.

Both clients walk the chain through `maxicrawler.app`: `CrawlService` for the
first half, `DownloadService` for the second, and `LibraryService` for reading
back what the chain produced.

Adding a host means adding a plugin and a provider. The last two stations do
not change.

## Next

-   Pinning the resolved address onto the connection that is opened, which is
    what closes DNS rebinding. The private-network guard raises the cost of
    reaching an internal address and does not make it impossible (ADR-031), and
    the difference is a custom `HTTPConnection` rather than another policy
-   A politeness schedule shared across concurrent crawls. Today a `HostSchedule`
    lives per crawl, which is exactly right while `serve` runs one worker and
    wrong the moment it runs two
-   Sitemaps, which robots.txt already tells us about and nothing yet reads
-   Real schema versioning for the SQLite metadata database. Today each adapter
    creates its tables with `CREATE TABLE IF NOT EXISTS` and declares the
    columns it has added since, which covers an appended column and nothing
    else. Renaming a column, changing a type, or backfilling a value needs a
    `user_version` and an ordered list of migrations — the same discipline
    `library.json` already has (ADR-013)
-   Per-page persistence — one `save_page` member on `CrawlRepository`, one
    call in the engine loop, and one table; `PageOutcome` already exists
-   A priority frontier, and a persistent one for resumable crawls — both are
    the same three-method protocol
-   Making `DiscoveryPipeline` thread-safe, which parallel crawling needs
    before a frontier can be drained by more than one worker
-   Crawl jobs — the unit the web interface manages, holding a `CrawlSession`
    beside its discovery results and its downloads. Today's registry is memory
    only: after a restart the pages fall back to what the database holds, and
    a crawl that was running is shown as abandoned. Downloads have the same
    shape and the same gap: a finished one is found again in the library, but
    its own page dies with the process
-   An index over the library, as a cache and never as the authority (ADR-010).
    Every listing reads one metadata document per stored resource: about 0.3
    seconds for two thousand entries warm, and roughly sixteen the first time a
    virus scanner sees them. Invalidated by modification time, it would turn the
    second listing into a `stat` per entry
-   `library` commands — list, verify, prune. `LibraryService` already answers
    the first two questions; what is missing is the command that asks them
-   More than one download at a time, which is the same subject as a queue: an
    order, a cancel, a resume, and something that survives a restart
-   Filtering and sorting the crawl list, which is the point at which htmx
    earns being vendored — the routes already render standalone fragments
-   Authentication, before the interface is anything but loopback. Until then
    `serve` refuses a public address unless `--allow-remote` asks for it
-   Further providers: Pixeldrain, GoFile, MediaFire
-   Parallel downloads — a thread pool around the drain loop; the queue and the
    worker are already built for it
-   Resume — HTTP range requests plus a byte offset in the metadata record; the
    staging directory already keeps a partial file out of the library
-   Provider-side integrity verification beside the recorded SHA-256, starting
    with Mega's meta-MAC
-   Persisting inspections so dead links can be detected over time

## Long-term

-   Distributed crawling
-   Plugin marketplace
-   Multi-user support, which is the same subject as authentication
