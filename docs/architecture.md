# Architecture

This document explains *how* the layers are built. [VISION.md](../VISION.md)
explains *why*: the "Clean Architecture", "Plugin First", and "Testability"
principles below are the direct implementation of its core principles.

MaxiCrawler follows a layered, modular design. Core packages must not depend on
optional delivery layers (`api` and `gui`). The crawler orchestrates work; it
does not embed parsing or storage details.

## Dependency direction

```text
config, utils
   ↑
downloader → crawler → extractors → documents
                    ↘ plugins (protocol, registry, resolver)
                    ↘ repository port ← database implements it structurally
plugins depend on the domain only; concrete plugins extend the protocol
cli composes crawler, documents, extractors, plugins and database
api and gui adapt the core for users
```

The crawler depends on the plugin *abstractions*, never on a concrete
plugin. Wiring the built-in plugin set is isolated in
`maxicrawler.plugins.defaults`, so the registry itself stays unaware of any
implementation.

The `database` package sits at the end of an inverted dependency: the crawler
declares the persistence port it needs and `database` satisfies it. The arrow
in the diagram points from the implementation to the abstraction, which is why
`database` may import the domain but the crawler never imports `database`.

## Current implementation boundary

The first implementation sprint provides configuration, logging, generic
SQLite metadata storage, plugin discovery, and a CLI. The `crawler`,
`downloader`, and `extractors` packages are explicit placeholders; no network
or crawling behavior is implemented yet.

`downloader` is still a placeholder. The `crawler` and `extractors` packages
were filled in by later sprints, as described below.

Sprint 2 introduces a pure domain layer and synchronous events. The discovery
pipeline is an in-memory application service: it accepts caller-provided URL
strings, normalizes and deduplicates them, then emits events. It does not fetch
URLs or schedule any I/O.

Sprint 3 adds the plugin architecture. It is a design sprint: no networking,
crawling, or downloading is implemented. Plugins classify URLs from their
string form only.

Sprint 4 adds offline discovery over local documents, described under
[Offline discovery](#offline-discovery). File-system reads are the only I/O
the project performs; there is still no network access.

## Plugin architecture

### Layers

| Element | Module | Layer |
| --- | --- | --- |
| `PluginInfo`, `UrlClassification`, `PluginResolution`, `UrlCategory`, `PluginCapability` | `maxicrawler.domain.plugins` | Domain |
| `CrawlerPlugin` | `maxicrawler.plugins.protocol` | Domain-facing contract |
| `PluginRegistry`, `PluginResolver` | `maxicrawler.plugins` | Application |
| `GenericPlugin` | `maxicrawler.plugins.generic` | Built-in plugin |
| `create_default_registry` | `maxicrawler.plugins.defaults` | Composition |

The domain imports nothing outside the standard library. Plugins import the
domain and the standard library only; they must not reach for HTTP clients,
databases, or the file system.

### The plugin contract

`CrawlerPlugin` is a `Protocol`, so plugins are structurally typed and need no
base class:

```python
class CrawlerPlugin(Protocol):
    @property
    def metadata(self) -> PluginInfo: ...
    def can_handle(self, record: UrlRecord) -> bool: ...
    def classify(self, record: UrlRecord) -> UrlClassification: ...
```

All three members must be side-effect free. `classify` reports
`UrlCategory.UNSUPPORTED` for records it cannot process instead of raising.

### Resolution order

`PluginRegistry` orders plugins by descending `PluginInfo.priority`; equal
priorities keep their registration order. `GenericPlugin` registers at
priority `-100`, so any specialised plugin outranks it. `PluginResolver`
asks the registry for the responsible plugin and returns an immutable
`PluginResolution` — with `plugin` and `classification` set to `None` when no
plugin claims the record.

### Two plugin contracts

`Plugin` (`maxicrawler.plugins.base`) remains the distribution-level entry
point contract used by `PluginLoader`. Its `register()` hook is where a
distribution adds its `CrawlerPlugin` implementations to a registry. The two
contracts answer different questions: *how is a plugin discovered on the
system* versus *what can a plugin decide about a URL*.

## Offline discovery

Sprint 4 adds the first end-to-end workflow. It is still free of networking,
crawling, and downloading: every URL is found by reading local files.

### Pipeline

```text
DocumentLoader → Document → GenericUrlExtractor → UrlCandidate
    → DiscoveryPipeline.discover()  (normalize, deduplicate, resolve plugin)
    → DiscoveryRepository.save_result()
```

`LocalDiscoveryService` is pure orchestration. It owns no parsing, no
normalization, and no storage logic; it only sequences the collaborators above
and tallies the result.

### Why the reader layer and the extractor are separate

Readers resolve *format* differences, the extractor resolves *URL* questions.
A `Document` carries prose in `text` and markup link targets in `links`, so one
generic extractor serves every format. The alternative — one extractor per
format — would have duplicated format knowledge on both sides of the boundary.

`Document` lives in `maxicrawler.documents`, not in the domain, because it
carries a filesystem `Path`. Keeping it out of `maxicrawler.domain` preserves
the rule that the domain knows no infrastructure.

### Where duplicates are removed

Deduplication happens at two levels, on purpose:

1. The extractor removes duplicates **within one document**, where a single
   element can yield the same URL twice — `<a href="x">x</a>` contributes both
   a link target and visible text.
2. `DiscoveryPipeline` removes duplicates **across documents** and counts them,
   which is what the `Duplicates removed` figure reports.

Moving step 2 into the extractor would have made that figure impossible to
report.

### Persistence

`maxicrawler.crawler.DiscoveryRepository` is declared next to its consumer.
`SQLiteDiscoveryRepository` satisfies it structurally and does not import it,
so `database` stays independent of discovery and the discovery layer contains
no storage logic. `NullDiscoveryRepository` is the default, keeping the service
usable and testable without a database. The CLI is the composition root that
binds the two.

## Design rules

1. Keep public interfaces typed and small.
2. Isolate I/O behind protocols or concrete adapters.
3. Keep parsing in `extractors`; do not put it in the crawler.
4. Treat plugins as untrusted extension boundaries; the registry validates
   registrations at runtime and rejects objects that break the protocol.
5. Keep optional UI and API dependencies out of the core package.
6. Add host support by registering a plugin, not by editing the pipeline.
7. Declare persistence ports next to their consumer; adapters satisfy them
   structurally and are bound by the composition root.
8. Add an input format by adding a `DocumentReader`, not by teaching the
   extractor about the format.
