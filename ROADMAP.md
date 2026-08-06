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
-   0.9 Desktop GUI
-   0.10 Scheduler & Automation
-   0.11 REST API
-   1.0 Stable Release

## The chain

```text
Website → Crawler → Discovery → Plugin → Provider → Download Manager → Library
```

Each station answers exactly one question:

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

Adding a host means adding a plugin and a provider. The last two stations do
not change.

## Next

-   Recursive crawling — a frontier modelled on `DownloadQueue`, plus a depth
    limit in the existing `CrawlPolicy`; the crawler itself does not change
-   robots.txt — a `RobotsPolicy` behind the same one-method seam, reading
    through the same fetcher
-   Per-host politeness — a `PageFetcher` that wraps another one, so neither
    the crawler nor the crawl loop learns about timing
-   Making `DiscoveryPipeline` thread-safe, which parallel crawling needs
    before a frontier can be drained by more than one worker
-   Further providers: Pixeldrain, GoFile, MediaFire
-   Parallel downloads — a thread pool around the drain loop; the queue and the
    worker are already built for it
-   Resume — HTTP range requests plus a byte offset in the metadata record; the
    staging directory already keeps a partial file out of the library
-   Provider-side integrity verification beside the recorded SHA-256, starting
    with Mega's meta-MAC
-   `library` commands: list, verify, prune
-   Persisting inspections so dead links can be detected over time

## Long-term

-   Distributed crawling
-   Web UI
-   Plugin marketplace
-   Multi-user support
