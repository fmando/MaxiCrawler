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
downloader → crawler → extractors
                    ↘ database
                    ↘ plugins (protocol, registry, resolver)
plugins depend on the domain only; concrete plugins extend the protocol
api and gui adapt the core for users
```

The crawler depends on the plugin *abstractions*, never on a concrete
plugin. Wiring the built-in plugin set is isolated in
`maxicrawler.plugins.defaults`, so the registry itself stays unaware of any
implementation.

## Current implementation boundary

The first implementation sprint provides configuration, logging, generic
SQLite metadata storage, plugin discovery, and a CLI. The `crawler`,
`downloader`, and `extractors` packages are explicit placeholders; no network
or crawling behavior is implemented yet.

Sprint 2 introduces a pure domain layer and synchronous events. The discovery
pipeline is an in-memory application service: it accepts caller-provided URL
strings, normalizes and deduplicates them, then emits events. It does not fetch
URLs or schedule any I/O.

Sprint 3 adds the plugin architecture. It is a design sprint: no networking,
crawling, or downloading is implemented. Plugins classify URLs from their
string form only.

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

## Design rules

1. Keep public interfaces typed and small.
2. Isolate I/O behind protocols or concrete adapters.
3. Keep parsing in `extractors`; do not put it in the crawler.
4. Treat plugins as untrusted extension boundaries; the registry validates
   registrations at runtime and rejects objects that break the protocol.
5. Keep optional UI and API dependencies out of the core package.
6. Add host support by registering a plugin, not by editing the pipeline.
