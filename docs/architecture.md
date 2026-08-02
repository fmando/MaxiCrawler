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
providers → plugins (pure URL parsing only); never the reverse
plugins depend on the domain only; concrete plugins extend the protocol
cli composes crawler, documents, extractors, plugins, providers and database
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

Sprint 5 adds the first provider plugin, described under
[Provider plugins](#provider-plugins). It classifies URLs from their string
form; no request is made to the provider.

Sprint 6 adds the provider layer, described under
[The provider layer](#the-provider-layer). This is the first sprint that
performs network access — from one command only, and never to download.

## Plugin architecture

### Layers

| Element | Module | Layer |
| --- | --- | --- |
| `PluginInfo`, `UrlClassification`, `PluginResolution`, `UrlCategory`, `PluginCapability` | `maxicrawler.domain.plugins` | Domain |
| `CrawlerPlugin` | `maxicrawler.plugins.protocol` | Domain-facing contract |
| `PluginRegistry`, `PluginResolver` | `maxicrawler.plugins` | Application |
| `GenericPlugin` | `maxicrawler.plugins.generic` | Built-in fallback plugin |
| `MegaPlugin` | `maxicrawler.plugins.mega` | Built-in provider plugin |
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
priority `-100` and provider plugins above zero — `MegaPlugin` at `100` — so
any specialised plugin outranks the fallback. `PluginResolver`
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

## Provider plugins

`maxicrawler.plugins.mega` is the reference implementation of a provider
plugin and the shape later providers should follow.

### Package layout

A provider is a package, not a module, because it grows:

```text
plugins/mega/
    models.py   MegaLink, MegaLinkKind, MegaLinkFormat — what a link is
    parser.py   parse_mega_url() — recognizing the URL, a pure string operation
    plugin.py   MegaPlugin — the CrawlerPlugin implementation
```

The split keeps the parser testable on its own: the bulk of provider knowledge
lives in `parser.py` and needs no registry, no record, and no plugin instance.

### Structured metadata

`UrlClassification.attributes` carries what a plugin read out of a URL as
plain name/value pairs, and `UrlCategory` says what kind of thing the URL is.
The domain deliberately learns no provider vocabulary: "handle" and "key" are
strings whose meaning belongs to the plugin that produced them. Typed
provider models such as `MegaLink` stay inside the provider package.

### Recognition rules

Two rules keep provider plugins predictable:

1. **Strict about identity, lenient about the key.** A modern Mega link is
   identified by its path, so an unreadable fragment yields a link without a
   key rather than a rejection. A legacy link keeps its identity in the
   fragment, so an unreadable fragment means the URL is not recognized.
2. **Decline what you do not understand.** `can_handle` returns `False` for
   URLs on a provider's host that are not shares, so the generic fallback keeps
   handling them. A provider plugin owns a *link shape*, not a domain name.

The category describes the share itself. A folder link that selects one entry
stays a `CONTAINER`; the selection is reported through the `node_handle` and
`node_kind` attributes. Encoding the selection in the category would have made
the modern and legacy forms disagree, because the legacy format does not state
what the selected node is.

### Fragments carry identity

`normalize_url` preserves the fragment. This is not cosmetic: a legacy Mega
share keeps its entire handle and decryption key there, so discarding the
fragment made unrelated shares compare equal, and the extractor dropped them
before any plugin could see them. Fragments are appended verbatim because keys
are case-sensitive.

The cost is accepted deliberately: `…/a#intro` and `…/a` now count as two URLs.

Plugins parse `UrlRecord.raw_url` rather than `normalized_url`, so a key
survives even if normalization rules change again.

## The provider layer

Sprint 6 adds MaxiCrawler's second extension layer. The two layers answer
different questions and are therefore separate:

| Layer | Package | Question | I/O |
| --- | --- | --- | --- |
| Plugin | `maxicrawler.plugins` | *"Can I classify this URL?"* | none |
| Provider | `maxicrawler.providers` | *"What can I do with this resource?"* | network allowed |

`UrlClassification` is the seam between them. A plugin decides from the URL
string alone and therefore runs on every URL discovery finds; a provider may
contact the host and is invoked only by a command that says so. Folding both
into one protocol would have made classification able to block on the network,
which the offline discovery workflow depends on not doing.

### Layers

| Element | Module | Layer |
| --- | --- | --- |
| `ResourceRef`, `ResourceSecret`, `ResourceMetadata`, `ResourceEntry`, `ResourceInspection`, `Availability`, `ResourceKind`, `ProviderInfo`, `ProviderCapability` | `maxicrawler.domain.providers` | Domain |
| `ResourceProvider` | `maxicrawler.providers.protocol` | Domain-facing contract |
| `ProviderRegistry` | `maxicrawler.providers.registry` | Application |
| `HttpTransport`, `UrllibTransport` | `maxicrawler.providers.transport` | Infrastructure |
| `CipherBackend`, `CryptographyCipherBackend` | `maxicrawler.providers.crypto` | Infrastructure |
| `RetryPolicy`, `Retrier` | `maxicrawler.providers.retry` | Application policy |
| `MegaProvider` | `maxicrawler.providers.mega` | Built-in provider |
| `create_default_provider_registry` | `maxicrawler.providers.defaults` | Composition |

The domain vocabulary carries no provider knowledge: a Mega share, a
Pixeldrain file, and a GoFile folder are all described with the same value
objects.

### The provider contract

```python
class ResourceProvider(Protocol):
    @property
    def metadata(self) -> ProviderInfo: ...
    def supports(self, classification: UrlClassification) -> bool: ...
    def reference(self, classification: UrlClassification) -> ResourceRef: ...
    def inspect(self, ref: ResourceRef) -> ResourceInspection: ...
```

The contract splits into a pure half and an I/O half on purpose. `supports`
and `reference` are side-effect free, so references can be built, stored, and
compared offline — which is exactly what `info --offline` does. `inspect` is
the single place a request happens, which keeps the network on one testable
seam and gives downloading a natural home later.

### Errors versus availability

A resource that was deleted, revoked, or blocked is a valid *answer*, so it is
reported through `Availability`. Exceptions are reserved for failures on our
side:

| Situation | Reported as |
| --- | --- |
| Deleted, revoked, blocked, over quota, rate limited | `Availability` value |
| Connection refused, timeout, HTTP error | `ProviderTransportError` |
| Response we cannot parse | `ProviderProtocolError` |
| Optional dependency missing | `ProviderDependencyError` |
| Malformed key or undecryptable payload | `ProviderCryptoError` |

`Availability.is_determined` separates "the link is dead" from "we could not
find out", which the CLI turns into exit codes `2` and `3`.

### Secret confinement

A share link can carry a decryption key in its URL fragment, which no HTTP
client transmits. MaxiCrawler preserves that property inside the process:

1. `ResourceSecret` exposes its value only through `reveal()`, is immutable,
   and redacts `repr()` and `str()`.
2. `ResourceRef.url` is the share URL with the fragment already removed. For a
   legacy Mega link, whose entire identity lives in the fragment, the canonical
   modern form is rebuilt instead of stripped.
3. `providers.mega.api` owns the wire and never imports `ResourceSecret`;
   `providers.mega.crypto` owns decryption and never imports a transport.
4. The provider is the only module that calls `reveal()`.

Point 4 is asserted, not assumed: `tests/test_mega_secret_confinement.py`
scans every outgoing request, rendering, and log record for eight-character
runs of the key, and reads the syntax tree of every source module to confirm
the allowlist. Widening that allowlist requires editing the test on purpose.

### The Mega provider

```text
providers/mega/
    api.py       MegaApiClient — the /cs wire protocol, and nothing else
    crypto.py    key unpacking and attribute decryption, entirely local
    mapping.py   Mega node types and status codes → domain models
    provider.py  MegaProvider — orchestration of the three above
```

Mega publishes no specification for this endpoint; the request shapes follow
its own open-source clients. Confining that knowledge to `api.py` means a
change on Mega's side has one place to be fixed, and a response that no longer
fits raises `ProviderProtocolError` rather than being guessed at.

Two requests exist, and neither transfers content:

| Link | Request | Answer |
| --- | --- | --- |
| File share | `{"a":"g","p":<handle>}` | size and encrypted attributes |
| Folder share | `{"a":"f","c":1,"r":1}` with `?n=<handle>` | the whole node tree |

The `g` download flag is deliberately unset, so no transfer URL is allocated
and no quota is consumed. Sizes, timestamps, and structure arrive unencrypted;
only names need the key, so a share published without one is still fully
enumerable.

A file inside a shared folder is described from the folder listing rather than
by asking about it directly, because its per-node key is published only there.

### Why the parser is reused, not duplicated

`providers.mega` imports `parse_mega_url` from `plugins.mega`. The URL grammar
of a host is one piece of knowledge and belongs in one place; duplicating it
would let the two layers disagree about what a link is. The dependency runs
providers → plugins and never the reverse, so no cycle is possible.

### Adding a provider

Nothing in the protocol, the registry, or the CLI is Mega-specific. A provider
for Pixeldrain, GoFile, or MediaFire implements the same four members, leaves
`ResourceRef.secret` as `None` because those hosts do not encrypt names, and
never touches the cipher backend. Registering it in
`create_default_provider_registry` is the only wiring required.

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
9. A provider plugin owns a link shape, not a host. Decline URLs you do not
   understand so the generic fallback keeps working.
10. Keep provider vocabulary inside the provider package; the domain carries
    it only as untyped attributes.
11. Classification never performs I/O; only a provider may reach a host, and
    only through `HttpTransport`.
12. Report what a resource *is* as a value and what *we* failed at as an
    exception.
13. A credential from a URL is wrapped in a `ResourceSecret` and unwrapped in
    exactly one module. Anything that echoes a URL echoes it without its
    fragment.
14. Providers may depend on plugins for pure URL parsing; plugins never depend
    on providers.
