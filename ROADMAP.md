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
-   0.8 Desktop GUI
-   0.9 Scheduler & Automation
-   0.10 REST API
-   1.0 Stable Release

## The chain

```text
Website / URL → Discovery → Plugin → Provider → Download Manager → Library
```

Each station answers exactly one question:

-   **Discovery** — *"Which URLs exist?"* Reads local documents; never leaves
    the machine.
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
