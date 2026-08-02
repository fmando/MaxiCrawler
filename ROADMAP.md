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
-   0.5 HTTP Engine 🚧 — transport, retries, and metadata inspection are in
    place; downloading is not
-   0.6 First Host Plugins ✅ — Mega is classified by a plugin and inspected by
    a provider
-   0.7 Desktop GUI
-   0.8 Scheduler & Automation
-   0.9 REST API
-   1.0 Stable Release

## Layers

MaxiCrawler grows along two extension layers, each answering its own question:

-   **Plugin** — *"Can I classify this URL?"* Pure, offline, runs on every
    discovered URL.
-   **Provider** — *"What can I do with this resource?"* May contact the host;
    invoked only by commands that perform network access.

## Next

-   Downloading through the provider layer (`ProviderCapability.DOWNLOAD`)
-   Further providers: Pixeldrain, GoFile, MediaFire
-   Persisting inspections so dead links can be detected over time

## Long-term

-   Distributed crawling
-   Web UI
-   Plugin marketplace
-   Multi-user support
