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
downloader → providers → plugins (pure URL parsing only); never the reverse
          ↘ library → domain
          ↘ documents, extractors (reused for reading a source of links)
web.engine → web.service → web.fetcher / parser / resolve / encoding
           ↘ web.frontier, web.session, web.report, web.policy
           ↘ web.repository port ← database implements it structurally
web → crawler (pipeline, repository port, summary)
    ↘ extractors (the prose-URL rule), events
    ↘ domain, utils; never providers, downloader, or library
crawler → extractors → documents
       ↘ plugins (protocol, registry, resolver)
       ↘ repository port ← database implements it structurally
plugins depend on the domain only; concrete plugins extend the protocol
cli composes web, crawler, documents, extractors, plugins, providers,
    downloader, library and database
api and gui adapt the core for users
```

The processing chain the packages implement runs left to right:

```text
Website → Crawler → Discovery → Plugin → Provider → Download Manager → Library
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

The `crawler` and `extractors` packages were filled in by later sprints,
`downloader` by Sprint 7, the `web` package by Sprint 8, and the crawl engine
inside it by Sprint 9, as described below.

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

Sprint 7 adds the download manager and the library, described under
[The download layer](#the-download-layer) and [The library](#the-library).
This is the first sprint that transfers content and writes files outside the
metadata database.

Sprint 8 adds the web layer, described under
[The web layer](#the-web-layer). It is the first station of the chain and the
first code that retrieves a document MaxiCrawler was not given. It fetches
exactly one page and downloads nothing.

Sprint 9 adds the crawl engine, described under
[The crawl engine](#the-crawl-engine). It makes the crawler recursive purely by
addition: a frontier, a visited set and a loop above the existing service,
which itself keeps answering one question about one page.

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
    def download(self, ref: ResourceRef, sink: DownloadSink) -> ContentDescriptor: ...
```

The contract splits into a pure half and an I/O half on purpose. `supports`
and `reference` are side-effect free, so references can be built, stored, and
compared offline — which is exactly what `info --offline` does. `inspect` and
`download` are the only places a request happens, which keeps the network on
two testable seams.

Three shapes in `download` are deliberate:

1. **A container is not a transfer.** Folders are enumerated with `inspect`
   and their entries downloaded individually, so *"what does one transfer
   mean?"* has the same answer for every provider.
2. **Unreachability raises**, which is the opposite of `inspect`. There is no
   partial answer to give for a transfer, and the caller has a failed download
   to record either way.
3. **Downloading is optional.** A provider that cannot move content omits
   `ProviderCapability.DOWNLOAD` and raises `UnsupportedResourceError`. The
   Mega provider computes that capability from what it was actually given, so
   an inspection-only composition advertises the truth rather than failing when
   asked.

### Two transports, not one

```python
class HttpTransport(Protocol):
    def post_json(self, url, payload, *, params=None, headers=None) -> object: ...


class StreamTransport(Protocol):
    def stream(self, url, *, chunk_size=...) -> Generator[bytes, None, None]: ...
```

An API call is a small JSON document read into memory whole; a transfer is
unbounded and must never be. Keeping them apart preserves the response-size
bound that protects the first, and means a provider composed for metadata alone
has no way to move content — which is what keeps `info` unable to download by
construction rather than by convention.

`stream` returns a generator on purpose: a caller that abandons a transfer
closes it and the socket goes with it.

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

Mega publishes no specification for this endpoint; the request shapes follow
its own open-source clients. Confining that knowledge to `api.py` means a
change on Mega's side has one place to be fixed, and a response that no longer
fits raises `ProviderProtocolError` rather than being guessed at.

```text
providers/mega/
    api.py       MegaApiClient — the /cs wire protocol, and nothing else
    crypto.py    key unpacking and attribute decryption, entirely local
    download.py  AES-128-CTR decryption of content, entirely local
    mapping.py   Mega node types and status codes → domain models
    provider.py  MegaProvider — orchestration of the four above
```

Four requests exist, and only the last two transfer content:

| Purpose | Request | Answer |
| --- | --- | --- |
| Describe a file share | `{"a":"g","p":<handle>}` | size and encrypted attributes |
| Describe a folder share | `{"a":"f","c":1,"r":1}` with `?n=<handle>` | the whole node tree |
| Transfer a file share | `{"a":"g","g":1,"p":<handle>}` | the above plus a transfer URL |
| Transfer a folder entry | `{"a":"g","g":1,"n":<node>}` with `?n=<handle>` | the same |

For inspection the `g` download flag is deliberately unset, so no transfer URL
is allocated and no quota is consumed. Setting it is what starts costing the
share's quota, which is why it appears only in `download`.

Sizes, timestamps, and structure arrive unencrypted; only names need the key,
so a share published without one is still fully enumerable — but not
downloadable, because its content stays sealed. That is reported as an
unsupported reference rather than attempted.

A file inside a shared folder is described *and* keyed from the folder listing
rather than by asking about it directly, because its per-node key is published
only there. The listing is fetched before the transfer is allocated, so a link
whose key cannot be resolved never costs the owner any quota.

Content is AES-128-CTR with the counter block set to the eight-byte nonce
followed by eight zero bytes. Counter mode turns the block cipher into a stream
cipher, so a download is decrypted as it arrives, at whatever chunk boundaries
the network produced, and written straight to disk. Nothing is buffered: a
fifty-gigabyte share costs the same memory as a fifty-kilobyte one.

Two wire details would otherwise be read as success. A transfer answer states
its URL in `g`, occasionally wrapped in an array; and an exhausted quota is
reported as `{"e": -17}` inside an otherwise valid result, which without a
check would arrive as an empty file.

### Why the parser is reused, not duplicated

`providers.mega` imports `parse_mega_url` from `plugins.mega`. The URL grammar
of a host is one piece of knowledge and belongs in one place; duplicating it
would let the two layers disagree about what a link is. The dependency runs
providers → plugins and never the reverse, so no cycle is possible.

### Adding a provider

Nothing in the protocol, the registry, the download manager, the library, or
the CLI is Mega-specific. A provider for Pixeldrain, GoFile, or MediaFire
implements the same five members, leaves `ResourceRef.secret` as `None` because
those hosts do not encrypt names, and never touches the cipher backend.
Registering it in `create_default_provider_registry` is the only wiring
required.

## The download layer

Sprint 7 adds the station that answers *"how are downloads executed?"*.

### Layers

| Element | Module | Layer |
| --- | --- | --- |
| `DownloadStatus`, `ContentDescriptor`, `Checksum` | `maxicrawler.domain.downloads` | Domain |
| `DownloadSink` | `maxicrawler.providers.protocol` | Domain-facing contract |
| `DownloadJob`, `DownloadOutcome`, `DownloadPlan`, `DownloadReport` | `maxicrawler.downloader.models` | Application |
| `SourceResolver` | `maxicrawler.downloader.sources` | Application |
| `DownloadPlanner` | `maxicrawler.downloader.planner` | Application |
| `DownloadQueue` | `maxicrawler.downloader.queue` | Application |
| `DownloadWorker`, `DownloadManager` | `maxicrawler.downloader.manager` | Application |
| `LibrarySink` | `maxicrawler.downloader.sink` | Infrastructure |
| `ProgressReporter`, `RichProgressReporter` | `maxicrawler.downloader.progress` | Interface adapter |

### The rule that keeps it provider-independent

**Nothing in `maxicrawler.downloader` branches on a provider name.** Where
behaviour differs between hosts it is asked for through `ResourceProvider` or
declared through `ProviderCapability`. That is a rule you can check by reading:
grep the package for `"mega"` and the only hit is a test.

`DownloadSink` is the seam that makes it work. A provider streams bytes into a
sink it does not own, so it never learns where they land; the manager owns the
destination, the staging file, the hashing, and the progress bar, so it never
learns how the bytes were obtained.

### Pipeline

```text
source string
  → SourceResolver.resolve()     a URL, a document, or a directory → SourceItem[]
  → DownloadPlanner.plan()       classify, resolve a provider, expand containers
  → DownloadQueue                ordered, duplicate-free backlog
  → DownloadWorker.execute()     skip, or provider.download() → LibrarySink
  → Library                      metadata.json + content/
```

### One command, one argument

From the outside, the difference between "a link" and "a file full of links" is
not interesting: both answer *"what should I download?"* with a list of URLs.
`SourceResolver` settles it once, and reuses the discovery readers and
extractor to do it — so whatever `discover` finds in a file is exactly what
`download` will fetch from it, rather than a second, subtly different scanner.

A Windows path such as `C:\links.txt` parses as a URL with the scheme `c`, so
the scheme is checked against `http` and `https` rather than merely being
required to exist.

### Planning is separate from running

Every decision that can go wrong — an unclassifiable URL, a provider that
cannot transfer, a revoked share, a folder holding no files — is made and
reported before a byte moves. That makes `--dry-run` the same code path minus
its last stage rather than a second implementation that can drift.

A failure during planning becomes an `UnresolvedSource`, never an exception:
one dead link in a list of two hundred must not stop the other hundred and
ninety-nine. The same applies during execution, where every job ends in a
`DownloadOutcome` whatever happened to it.

### The queue is built for workers it does not yet have

Only one worker drains the queue today, and that is deliberate: parallel
transfers to the same host are a policy question, not a performance trick. The
two things that make concurrency painful to retrofit are nevertheless already
handled, because both are structural rather than incidental:

1. **Mutable state without a lock.** Every queue operation is guarded, so
   adding a second worker changes no invariant.
2. **Workers that own their work.** Jobs are handed out one at a time through
   `pop()`, so a worker never holds a slice of the backlog and workers never
   have to agree on who takes what.

What is left to add is a thread pool around the drain loop in
`DownloadManager.run`, plus whatever ordering guarantee the report should keep.

Deduplication is by resource identity — provider, container, resource — rather
than by URL, so the same file reached through a link with a key and a link
without one queues a single time.

### Existing files

The worker asks the library whether it already holds the resource, and that
question needs no network request at all: re-running over a list of two hundred
already-downloaded links contacts nobody. Both the metadata record and the
payload file are checked, so a library whose file was deleted repairs itself by
simply running again.

Nothing is overwritten automatically. Overwrite options can be added later as
an argument to that check; the check itself is one function.

### Why resume is not implemented yet

It was deliberately left out, and the architecture was shaped so it can be
added rather than retrofitted:

- content is already staged under `.incomplete/`, which is where a partial file
  would have to live;
- `StreamTransport.stream` already takes a URL and returns chunks, so a byte
  offset is one parameter;
- `ResourceRecord` already versions itself and preserves unknown members, so a
  resumed-offset field costs no migration.

### Progress reporting

`ProgressReporter` is a protocol rather than a print statement, so the manager
stays usable from a script, a future GUI, and a future API without any of them
inheriting a terminal. `NullProgressReporter` is the default: a library caller
that asks for nothing gets nothing.

Rich renders to **standard error**. Standard output then carries only the final
report, which keeps `maxicrawler download … > report.txt` meaningful.

## The library

The last station answers *"how are resources stored and managed?"*. It knows
nothing about providers, transfers, or queues: a Mega file, a Pixeldrain file,
and a GoFile entry are stored by exactly the same rules.

### Layout

```text
<root>/
    library.json                 the store descriptor and its schema version
    mega/                        one namespace per provider
        aabbccdd-1a2b3c4d5e/     one directory per resource
            metadata.json        what this resource is and where it came from
            content/             the payload, as the provider named it
                ubuntu.iso
            .incomplete/         in-flight files; never a finished download
```

Four properties follow from it, and each is the reason a simpler layout was
rejected:

1. **The file system is the source of truth.** Every entry describes itself, so
   a library survives losing a database, can be moved with `rsync`, and stays
   readable with a text editor. An index may be added later as a cache, never
   as the authority.
2. **The payload and the metadata cannot collide.** A provider is free to name
   a file `metadata.json`; putting the payload in its own directory makes that
   harmless instead of destructive.
3. **A partial download is never mistaken for a finished one.** Content is
   written under `.incomplete/` and moved into place only once it is whole, so
   an interrupted run leaves nothing a later run would skip over.
4. **An entry is addressed by identity, not by name.** The directory key is
   derived from the reference alone, so renaming a remote file, or reading it
   through a link that carries no key, still finds the same entry.

### The entry key

```text
<slug>-<digest>       e.g.  aabbccdd-1a2b3c4d5e
```

Both halves are needed. The slug alone would not do: a Mega handle is
case-sensitive base64url, so `AbCdEfGh` and `abcdefgh` are different resources
that a case-insensitive volume — the default on Windows and macOS — would map
onto one directory, silently merging two downloads. The digest alone would do,
but nobody could read the result; keeping the stem means `ls` on a provider
directory still says something.

The digest is `sha256(provider \0 parent \0 resource)` truncated to ten hex
characters. The credential is deliberately not part of the input, so two links
to the same resource — one with a key, one without — address the same entry.

### Why this layout and not another

| Rejected | Why |
| --- | --- |
| A flat directory of files | Name collisions, nowhere to put metadata, no provider namespacing. |
| Mirroring the provider's own folder tree | Names are encrypted or absent for some links, remote trees get renamed and moved, Windows path limits are reached quickly, and two links to the same node yield two copies. The remote path is *recorded* in metadata instead, so a browsable view can be generated later. |
| A content-addressed store (`blobs/<sha256>`) | Perfect deduplication, but you cannot name a file before you have downloaded it, and the result is unbrowsable. The recorded SHA-256 leaves the door open for a deduplication pass later. |
| Hash-sharded directories (`mega/3f/3fa9…`) | Solves a problem the project does not have — millions of entries in one directory — at the cost of readability. Adding a shard level later is a rename of directories, which the `library.json` schema version makes tractable. |
| A SQLite index as the source of truth | A library must survive losing its database and be inspectable by hand. SQLite can still be added as a rebuildable cache. |
| Date-based buckets (`2026/08/…`) | Download date is not identity; re-downloading or repairing an entry would move it. |

### Every foreign name is sanitized

A payload name is decrypted from a remote host and is treated as hostile.
`maxicrawler.library.naming` is the single place a name becomes a path
component, and it guarantees three things:

- a component never escapes its directory, whatever the input contained —
  directory parts are stripped under both POSIX and Windows rules, so a name
  produced on one platform is stripped the same way on the other;
- two distinct resources never collide, not even on a case-insensitive volume;
- a component is legal on Windows, macOS, and Linux alike — reserved
  characters and device names are neutralised, trailing dots and spaces
  removed, and long names shortened while keeping their extension.

### The metadata document

```json
{
  "schema": 1,
  "provider": "mega",
  "key": "aabbccdd-1a2b3c4d5e",
  "resource_id": "AaBbCcDd",
  "parent_id": null,
  "kind": "file",
  "name": "ubuntu.iso",
  "source_url": "https://mega.nz/file/AaBbCcDd",
  "source_document": "docs/links.md",
  "status": "completed",
  "discovered_at": "2026-08-02T09:00:00+00:00",
  "downloaded_at": "2026-08-02T09:05:12+00:00",
  "attempts": 1,
  "error": null,
  "content": {
    "filename": "ubuntu.iso",
    "path": "content/ubuntu.iso",
    "size": 5800000000,
    "checksums": [{"algorithm": "sha256", "value": "…"}]
  }
}
```

Two properties make the format survivable for years:

1. **A schema version.** A document written by a newer MaxiCrawler is refused
   rather than misread, so an old binary can never quietly discard fields it
   does not understand.
2. **Unknown members are preserved.** Anything the current release does not
   recognise is kept in `ResourceRecord.extra` and written back unchanged, so a
   future field survives a round trip through today's code.

`source_url` is `ResourceRef.url`, which already has its fragment removed, so a
library directory is safe to share, back up, or paste into an issue.

The SHA-256 is computed while writing rather than by re-reading the file, which
costs nothing extra on a stream that is being written anyway. It is
provider-independent on purpose: a host that offers its own integrity check —
Mega's meta-MAC, for instance — would add to this rather than replace it, so
resources from different providers stay comparable.

## The web layer

Sprint 8 adds the first station of the chain: the one that answers *"which URLs
does this page contain?"*.

### Layers

| Element | Module | Layer |
| --- | --- | --- |
| `FetchedPage`, `PageInfo`, `ParsedHtml`, `HtmlDocument`, `PageLink`, `RawLink`, `LinkKind`, `CrawlResult` | `maxicrawler.web.models` | Application values |
| `PageFetcher` | `maxicrawler.web.fetcher` | Domain-facing contract |
| `UrllibPageFetcher`, `BoundedRedirectHandler` | `maxicrawler.web.fetcher` | Infrastructure |
| `detect_encoding`, `decode_body` | `maxicrawler.web.encoding` | Pure |
| `HtmlParser`, `HtmlLinkParser` | `maxicrawler.web.parser` | Pure |
| `resolve_links` | `maxicrawler.web.resolve` | Pure |
| `CrawlPolicy`, `PolicyDecision`, `AllowAllPolicy` | `maxicrawler.web.policy` | Application policy |
| `WebDiscoveryService` | `maxicrawler.web.service` | Application |
| `render_crawl`, `render_crawl_json` | `maxicrawler.cli.crawling` | Interface adapter |

### Why a package of its own

`maxicrawler.crawler` is described throughout this document as the station
whose I/O is the file system and which never leaves the machine. A socket
inside it would erase a boundary that is currently checkable. `maxicrawler.web`
sits beside it instead and imports `crawler`, `extractors`, `domain`, and
`utils` — never `providers`, `downloader`, or `library`. Grep the package for
`mega`, `provider`, `download`, or `library`: there are no hits.

### Pipeline

```text
crawl URL
  → CrawlPolicy.may_fetch()   may this be retrieved at all?
  → PageFetcher.fetch()       scheme, redirects, size, content type → FetchedPage
  → decode_body()             BOM → header → meta prescan → UTF-8
  → HtmlParser.parse()        tags and attributes → ParsedHtml (no URLs yet)
  → resolve_links()           base URL + relative resolution → HtmlDocument
  → DiscoveryPipeline         normalize, deduplicate, resolve a plugin
  → DiscoveryRepository       the same port `discover` writes through
  → CrawlResult
```

`WebDiscoveryService` is pure orchestration, deliberately the same shape as
`LocalDiscoveryService`. **The discovery pipeline is never bypassed**: a URL
found on a page is normalized, deduplicated, and classified by exactly the same
plugins, in the same order, as one found in a Markdown file, and one fetched
page counts as one processed document. That is what makes the two commands
report comparable numbers instead of two ideas of what a URL is.

### Everything about a fetch is bounded

Each limit exists because the page belongs to a stranger:

| Threat | Answer |
| --- | --- |
| `file:`, `data:`, `javascript:` targets | Scheme allow-list on the request **and** on every redirect hop |
| Redirect to another scheme | `BoundedRedirectHandler`; the standard one permits `ftp:` |
| Redirect loops | Hard cap, chain recorded, `TooManyRedirectsError` |
| A video answered to a page request | Content type checked from the headers **before** the body is read |
| Unbounded body | `max_page_bytes`, on `Content-Length` *and* while reading |
| Decompression bomb | Incremental decompression under the same limit |
| A page with a million links | `max_links`; the surplus is dropped and reported |
| Credentials in a log record | Every message carries `safe_target(url)` only |

Two details of `urllib` had to be worked around, and both are asserted rather
than assumed. Its redirect handler checks the scheme *before* `redirect_request`
is reached, so a `file:` target would arrive as a plain 302 unless
`http_error_302` pre-empts it; and it aliases 301, 303, 307, and 308 onto its
own method object, so overriding 302 alone would let four of the five statuses
bypass the check entirely.

### Encoding

The HTML standard's sniffing order, reduced to what a non-interactive fetcher
can do: a byte order mark, then the `charset` of the HTTP header, then a
prescan of the first 1024 bytes, then UTF-8. Browsers fall back to windows-1252
instead; the corpus this project targets is modern, and being wrong costs
little because a body that will not decode strictly is decoded again with
replacement characters. A page can never abort a crawl.

Every charset label passes through `codecs.lookup()`, because real responses
carry labels no codec is registered under.

### Parsing and resolution are separate

The parser is pure syntax: it collects the strings a page wrote down, in the
order it wrote them, and knows nothing about URLs. `resolve.py` turns those into
absolute URLs. The split means the parser is testable without a base URL and
the resolver without any HTML — and it is why `ParsedHtml` and `HtmlDocument`
are two types rather than one.

Parsing uses `html.parser`, which `HtmlDocumentReader` already uses for local
documents. That is the point: `discover` and `crawl` agree about what a link is
by construction. It is not a spec-compliant HTML5 tree builder and does not need
to be — link extraction reads start tags and attribute values. `HtmlParser` is a
protocol, so a faster backend can be substituted without touching the crawler.

The element table is data:

```python
LINK_SOURCES = {
    ("a", "href"): LinkKind.ANCHOR,
    ("area", "href"): LinkKind.ANCHOR,
    ("img", "src"): LinkKind.IMAGE,
    ("script", "src"): LinkKind.SCRIPT,
    ("link", "href"): LinkKind.STYLESHEET,
    ("iframe", "src"): LinkKind.FRAME,
}
```

Three resolution rules are decisions rather than details:

1. **Resolution is against the URL that answered**, not the one requested. A
   page reached through a redirect states its relative links against where it
   ended up. This is the most common relative-link bug in a crawler.
2. **A `<base>` overrides that, and the first one wins**, as the standard
   requires. A base that resolves to something other than HTTP(S) is ignored.
3. **Fragments are preserved.** A conventional crawler strips them; doing that
   here would silently destroy every legacy Mega share on a page, because such
   a link keeps its whole handle and key in the fragment. A reference that is
   *only* a fragment points into the page we already hold, so that one is
   dropped — and counted, like every other reference the pipeline cannot take.

`<link rel="canonical">` is recorded but never acted on. Treating it as identity
is a de-duplication policy belonging to a recursive crawl; applying it here
would report links under a URL that was never fetched.

### URLs written as prose

A share link on a forum page is usually written out rather than linked, so the
parser also collects the page's prose and the service scans it with
`maxicrawler.extractors.scan_text` — the same rule that finds a URL in a
Markdown file. A second scanner would eventually disagree about what a URL
looks like. Script and style content is excluded, so a URL inside a `<script>`
is not mistaken for one a reader can see. `crawl --no-prose` turns it off.

### The robots.txt extension point

```python
@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason: str | None = None


class CrawlPolicy(Protocol):
    def may_fetch(self, url: str) -> PolicyDecision: ...
```

That is the whole seam, and it is about twenty lines. What plugs into it:

- **`RobotsPolicy`** — reads `/robots.txt` through the *same* `PageFetcher` the
  crawl already uses, so no second I/O seam is needed, parses it with
  `urllib.robotparser`, and caches per host.
- **`ScopePolicy`** — same host, path prefix, maximum depth.
- **`PrivateNetworkPolicy`** — the SSRF guard, required before any web
  interface accepts a URL from someone other than the operator.
- **`CompositePolicy`** — the first refusal wins.

A refusal is a value, so a recursive crawl records *"skipped: disallowed by
robots.txt"* and carries on. The service raises `PolicyRefusedError` only for
the URL it was explicitly asked for, where refusing *is* a failure of the
request; a crawl loop catches that per URL.

robots.txt is deliberately not implemented. Fetching one page named by its
operator is what a browser does when the same person types the same address.

### How this extends to recursion

`crawl()` takes one URL, returns one immutable `CrawlResult`, and holds no
state about which URL to visit next. Recursion is therefore not a property of
the crawler but a question of who calls it, in what order — an addition *above*
the layer. Nothing in `fetcher`, `parser`, `resolve`, or `encoding` changes.

```python
frontier.push(CrawlItem(seed, depth=0))
while (item := frontier.pop()) is not None:
    try:
        result = service.crawl(item.url, session)
    except CrawlError as error:
        report.skipped(item.url, str(error))  # one dead page stops nothing
        continue
    frontier.extend(CrawlItem(link.resolved_url, item.depth + 1) for link in result.document.links)
```

| Needed for | Provided by | Changes in `maxicrawler.web` |
| --- | --- | --- |
| Multiple pages | A frontier — `DownloadQueue` again: lock-guarded, deduplicating by identity, handing out one item at a time | none |
| Scope and depth | `CrawlPolicy` | none |
| Politeness, `Crawl-delay` | A `PageFetcher` wrapping another one, like `Retrier` around the provider transport | none |
| A live web UI | `CrawlResult` is a value and the CLI is a pure renderer; `DiscoveryPipeline` already publishes `ScanStarted`, `UrlDiscovered`, and `ScanFinished` | `PageFetched` / `PageFailed` events |
| A resumable crawl | `DiscoveryRepository` already records every result with its `source_url`; a frontier table is an adapter | none |

`CrawlResult` states both `requested_url` and `final_url` for exactly this
reason. A redirect makes them differ, and a queue, a history, and an interface
each need a different one; deriving either after one has been dropped is
impossible.

**One honest caveat.** `DiscoveryPipeline` is not thread-safe: `_statistics` is
rebound without a lock and `DuplicateDetector` holds a plain `set`. Parallel
crawling will need either a lock there or one pipeline per worker with merged
`Statistics`. That is a contained change to an existing class, recorded now
rather than discovered later.

### What this sprint deliberately does not do

No recursion, no robots.txt, no cookies, no authentication, no forms, no
JavaScript, no headless browser, no sitemaps, no `srcset`, no canonical-URL
de-duplication, no conditional requests, and no connection reuse. A page that
builds its links in the browser will appear to have fewer than a reader sees.

## The crawl engine

Sprint 9 turns the single-page crawler into a recursive one, and does it
entirely by addition: not one line of `fetcher`, `parser`, `resolve` or
`encoding` changed.

### Layers

| Element | Module | Layer |
| --- | --- | --- |
| `CrawlItem`, `Frontier`, `FifoFrontier`, `VisitedSet`, `InMemoryVisitedSet`, `visit_key` | `maxicrawler.web.frontier` | Application |
| `CrawlSession`, `CrawlOptions`, `RequestContext`, `CrawlState`, `CrawlControl` | `maxicrawler.web.session` | Application values |
| `CrawlReport`, `PageOutcome`, `SkipReason`, `CrawlStatistics` | `maxicrawler.web.report` | Application values |
| `SameDomainPolicy`, `CompositePolicy` | `maxicrawler.web.policy` | Application policy |
| `CrawlEngine` | `maxicrawler.web.engine` | Application |
| `CrawlRepository`, `NullCrawlRepository` | `maxicrawler.web.repository` | Port |
| `SQLiteCrawlRepository` | `maxicrawler.database.crawls` | Infrastructure |
| `render_crawl`, `render_crawl_json` | `maxicrawler.cli.crawling` | Interface adapter |

### The one change to existing code

`WebDiscoveryService.crawl()` used to open a discovery session, fetch one page
and close it again. Called forty times that publishes forty `ScanStarted`
events and forty separate plugin tallies for what is one crawl. The session
bookkeeping therefore moved out:

```python
service.start(scan)  # once
service.crawl_page(url, scan)  # per page — fetch, decode, parse, resolve, discover
service.finish(scan)  # once
```

`crawl()` survives with its signature and behaviour intact; it is three lines
over the new pair. The service still answers exactly one question.

### The loop

```text
CrawlEngine.run(session)
  repository.start_crawl(session)
  service.start(scan_session)
  consider(CrawlItem(seed, depth=0))          → nothing queued? that is an error

  while not control.stop_requested:
      pages == max_pages?          → PAGE_LIMIT
      item = frontier.pop()        → None? COMPLETED
      already fetched under another URL? → skip
      result = service.crawl_page(item.url, scan_session)
      claim(item.url); claim(result.final_url)
      record PageOutcome
      for link in result.links: consider(CrawlItem(link, depth + 1))

  service.finish(scan_session)
  repository.finish_crawl(session, report)    → CrawlReport
```

### One gate, and every refusal counted

`_consider()` is the only place a URL is turned away:

```python
if item.depth > max_depth:
    skips[TOO_DEEP] += 1
elif not scope.may_fetch(item.url).allowed:
    skips[OUT_OF_SCOPE] += 1
elif not visited.register(visit_key(url)):
    skips[ALREADY_SEEN] += 1
else:
    frontier.push(item)
```

Two consequences worth stating. The frontier only ever holds URLs that will
really be fetched, which is what bounds its size without a cap. And a report
can say *why* a crawl of a large site stopped at four pages, rather than only
that it did.

The order is deliberate: depth first because it costs nothing and rejects the
most, identity last — so a URL refused for being off-site is *not* also
remembered as seen, and a later crawl under a wider scope still finds it. The
counters therefore count occurrences rather than distinct URLs.

### Identity: two keys, and two moments

`normalize_url` preserves URL fragments, because a legacy Mega share keeps its
handle and decryption key there. But `page#intro` and `page#setup` are one page
to fetch, so `visit_key()` strips the fragment. Discovery and the frontier
answer different questions and must not share a key.

Enqueue-time identity alone is not enough. When `/old` and `/new` are both
linked from a page, both are queued *before* anyone knows a redirect makes them
the same page. Pages that actually answered are therefore tracked separately
and re-checked when an item is popped.

`<link rel="canonical">` is recorded on the outcome and never acted on. It is a
claim by the page, not a fact about the URL, and skipping a URL never fetched
loses every outgoing link on it.

### Scope

`SameDomainPolicy` treats `www.example.org` and `example.org` as one site and
matches subdomains label-wise, so `evilexample.org` is *not* inside
`example.org` — the classic hole in a same-domain rule. It is not a registrable
domain in the Public Suffix List sense; computing that needs the list, which is
a dependency and a file that goes stale, so the limitation is documented and
only ever narrows the scope.

**It is off by default**, and that is a decision rather than an oversight.
MaxiCrawler serves two workflows equally: crawling one website, where staying
on it is the point, and hunting for share links, which live on Mega,
Pixeldrain and GoFile *by definition*. `--max-pages` and `--depth` are what
bound a crawl instead. The default is configurable through `crawl_same_domain`.

The engine derives the scope from the session rather than trusting its caller
to inject a matching policy. An option that silently does nothing unless wired
correctly would let a report and a database row claim a crawl stayed on one
host while it wandered off it.

### Ending, and stopping

| State | Meaning |
| --- | --- |
| `COMPLETED` | the frontier ran dry |
| `PAGE_LIMIT` | `--max-pages` was reached; `frontier_remaining` is non-zero |
| `INTERRUPTED` | Ctrl-C, or `CrawlControl.request_stop()` |

All three produce a full report. Hitting a limit is the crawl doing what it was
told, so it exits `0`; only an interruption gets a code of its own.

One dead page never stops a run — a failure becomes a `PageOutcome` and the
loop continues. The seed is the exception: a crawl whose starting point cannot
be read has nothing to report, so the caller gets an exception.

Ctrl-C is caught in the loop rather than in a signal handler. A handler is
global process state, hostile to a library caller and awkward to test;
`CrawlControl` gives a future Stop button exactly the same path.

### Persistence

A crawl stores its summary — what it was told, how it ended, its counters — in
`crawl_sessions`, keyed by the same identifier as its `scan_sessions` row, so
the two join without a second key and every URL a crawl found is reachable from
the crawl that found it.

Page outcomes are deliberately not stored yet. `PageOutcome` exists in memory
for every page because the report needs it, so adding them is one `save_page`
member, one call in the loop, and one table.

Note what *is* already stored: every discovered URL, with its plugin and its
category, through the existing discovery repository. Pages and links are
different things.

### Where authentication will go

Two seams, and the split is the point:

| | Seam | Holds |
| --- | --- | --- |
| **Data** | `RequestContext` on `CrawlSession` | headers today; a cookie jar, a credential, a proxy later |
| **Behaviour** | a `PageFetcher` decorator | performing a login, refreshing a CSRF token, retrying a 401 |

```python
fetcher = ThrottledFetcher(AuthenticatedFetcher(UrllibPageFetcher(...), credentials))
```

Neither `CrawlEngine` nor `WebDiscoveryService` changes by a line, because
`PageFetcher` is already a protocol and the engine only ever calls
`crawl_page()`. The crawler never learns *how* authentication works.

A report can reach a context by traversal, so the enforceable rule is that
**nothing serializing a report writes it**. The JSON renderer and the SQLite
adapter each assert that where they live: the database file is searched for the
secret, and both modules' syntax trees are checked for any read of the context.

### The `ThrottledFetcher` extension point

Deliberately not implemented in this sprint. Politeness, rate limits, robots.txt
and scheduling belong together and are one subject; splitting one of them off
early would settle the shape of the other three by accident.

The seam is already there and needs nothing new:

```python
class ThrottledFetcher:  # is itself a PageFetcher
    def fetch(self, url: str) -> FetchedPage:
        self._wait_for(host_of(url))
        return self._inner.fetch(url)
```

Rate limiting is not a `CrawlPolicy`, on purpose: *"may I fetch this?"* and
*"may I fetch it **yet**?"* are different questions, and waiting must not happen
inside a policy check.

### How a scheduler and a web interface plug in

| Concern | Where it goes | Engine changes |
| --- | --- | --- |
| Which URL next | a `Frontier` implementation | none |
| Politeness per host | `ThrottledFetcher` | none |
| Persistent queue, resumable crawl | `SqliteFrontier`, a persistent `VisitedSet` | none |
| Parallel workers | a thread pool around `pop()` | the loop only |
| Waiting rather than finishing | `next_available_in()` on `Frontier` | one branch |
| Live progress | `CrawlStarted`, `PageCrawled`, `PageFailed`, `CrawlFinished` on the event bus | none |
| A Stop button | `CrawlControl.request_stop()` | none |
| Crawl history | `CrawlRepository.stored_crawls()` | none |

`pop()` returning `None` currently means *"nothing left"*; a scheduler needs to
tell that apart from *"nothing yet"*. Recorded rather than hidden.

**The blocker for parallelism is named and is not the engine.**
`DiscoveryPipeline` is not thread-safe: `_statistics` is rebound without a lock
and `DuplicateDetector` holds a plain `set`. That needs a lock, or one pipeline
per worker with merged `Statistics`, before a second worker exists.

### Outlook: crawl jobs

A `CrawlSession` describes one crawl. It will most likely become part of a
larger **crawl job** — the unit a web interface manages, starts, stops and
lists:

```text
Job
 ├── CrawlSession      which seed, which limits, how it ended
 ├── Discovery         which URLs were found and classified
 ├── Download Queue    which of them should be fetched
 └── Result            what ended up in the library
```

The class keeps its name. A job is a bracket *around* a session, its discovery
and its downloads — not a rename of any of them, and none of the four stations
needs to learn anything about the others that it does not already know.

### Testing, and one mistake worth recording

The suite makes no outbound connections, and this sprint is where that stopped
being free. With links followed off-host by default, a fixture holding a real
URL quietly turns the test suite into a client of somebody else's server — which
is exactly what happened while these tests were being written.

Two things fixed it. Fixtures reach "elsewhere" through the *same* local server
under its other hostname: `127.0.0.1` and `localhost` are one machine but two
hosts, which exercises the scope rule without leaving it. And
`tests/test_no_outbound_connections.py` guards `socket.create_connection`, so a
repeat fails loudly instead of silently.

That guard immediately paid for itself: it found that `CrawlOptions.same_domain`
was honoured only by the CLI, so an engine used directly ignored it while the
report still claimed the crawl had stayed put.

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
    only through `HttpTransport` or `StreamTransport`.
12. Report what a resource *is* as a value and what *we* failed at as an
    exception.
13. A credential from a URL is wrapped in a `ResourceSecret` and unwrapped in
    exactly one module. Anything that echoes a URL echoes it without its
    fragment.
14. Providers may depend on plugins for pure URL parsing; plugins never depend
    on providers.
15. The download manager never branches on a provider name. Ask through the
    provider protocol, or declare through `ProviderCapability`.
16. A provider transfers into a sink it does not own; only the library decides
    a path.
17. Every name that arrives from a remote answer passes through
    `maxicrawler.library.naming` before it becomes a path component.
18. Content becomes visible only once it is whole. Stage it, verify the size,
    then move it into place.
19. A stored document states its schema version and preserves members it does
    not recognise.
20. One dead link never stops a run. Report it as a value and carry on.
21. The crawler knows no provider, no download, and no library. It retrieves a
    document and reports the URLs in it.
22. A fetch is bounded in every dimension: scheme, redirects, content type, and
    response size before *and* after decompression.
23. Politeness is a policy object, never a condition inside the fetch loop.
24. The crawler fetches one page and holds no state about the next one.
    Recursion belongs to the caller.
25. A URL found on a page goes through the same pipeline and the same plugins
    as one found in a file. Never write a second scanner.
26. Recursion is a loop above the crawler, never a parameter inside it.
27. The frontier orders; the visited set identifies. Never one class for both.
28. The key for *"already fetched"* is not the key for *"already discovered"*.
    Discovery keeps fragments; the frontier drops them.
29. Every URL a crawl turns away is counted with its reason.
30. An option must take effect where it is declared. Deriving scope from the
    session beats trusting a caller to inject a matching policy.
31. A session carries request context; nothing that serializes a report writes
    it. Assert that where the serializer lives.
32. Tests never leave this machine. "Elsewhere" is the same server under
    another hostname.
