# MaxiCrawler

**MaxiCrawler** is a modular Python 3.12+ foundation for building responsible,
extensible web crawlers. It keeps crawling, extraction, persistence, plugins,
and delivery interfaces separate so each concern can evolve independently.

> Version: **0.1.0** — the project is in its initial, pre-alpha phase.

MaxiCrawler is a link discovery and management platform, not merely a
downloader. [VISION.md](VISION.md) states the mission, the core principles, and
what the project deliberately will not do.

## Features

- Clear package boundaries for crawling, extraction, downloads, storage, plugins, and delivery layers.
- Two clients over one implementation: a command line and a web interface, both calling the same services.
- A recursive web crawler with a pluggable frontier, depth and scope limits, and a crawl summary you can stop, resume the design of, and store.
- Responsible by default: robots.txt is obeyed, a host's own `Crawl-delay` is waited out, and loopback, private and cloud-metadata addresses are refused — on the URL and on every redirect.
- Links found on a page feed the same discovery pipeline and the same plugins local documents use.
- A provider-independent download manager: a new host is a plugin and a provider, nothing else.
- A self-describing library: one directory per resource, with versioned JSON metadata beside it.
- A searchable library in the browser, and a viewer that lets the browser display what it can — no renderer of our own.
- A crawl report you can search, filter, sort, page and bookmark, with every link classified by what it points at.
- A download queue you can reorder, pause and retry, and one click to queue everything a filter matches.
- Downloads for the rest of the web: any file at a plain HTTP(S) URL, through the same library, behind the same private-network guard.
- A library you can work through: tiles or rows, filters for kind, size and verdict, four judgements that survive the next download, and a discard that takes the bytes back and is not fetched again.
- Thumbnails made by a run of their own, so a page of tiles over a library of photographs costs megabytes rather than gigabytes — and stays a cache that can be deleted in full at any time.
- Typed interfaces and strict static checking with mypy.
- Fast formatting and linting with Ruff.
- Test-first baseline with pytest.
- Reproducible dependency management through uv.
- Automated quality checks for Python 3.12 and 3.13 via GitHub Actions.

## Quick start

Install [uv](https://docs.astral.sh/uv/) and clone the repository:

```bash
git clone https://github.com/fmando/MaxiCrawler.git
cd MaxiCrawler
uv sync --all-extras
```

`--all-extras` includes four optional extras. `mega` pulls in `cryptography`
and is needed only to decrypt the names inside a Mega share. `brotli` lets the
crawler read a Brotli-compressed page; without it, `Accept-Encoding` simply
does not advertise `br`, so a server sends gzip instead. `web` is the browser
interface. `thumbnails` pulls in Pillow, which is what makes the small copies a
tile shows; without it a tile falls back to the stored image or a symbol, which
is what it did before they existed. Everything else works without any of them.

Create the local configuration and SQLite metadata database:

```bash
uv run maxicrawler init
uv run maxicrawler config
uv run maxicrawler version
```

Discover the URLs in a folder of local documents:

```bash
uv run maxicrawler discover ./docs
```

Discover the URLs on a web page, or crawl a site two levels deep:

```bash
uv run maxicrawler crawl https://example.org
```

```bash
uv run maxicrawler crawl https://example.org --depth 2 --same-domain
```

Ask what a share link points at, without downloading it:

```bash
uv run maxicrawler info "https://mega.nz/file/<handle>#<key>"
```

Download it:

```bash
uv run maxicrawler download "https://mega.nz/file/<handle>#<key>"
```

Or do the same things in a browser:

```bash
uv run maxicrawler serve
```

```text
MaxiCrawler is listening on http://127.0.0.1:8000/
```

Run the test suite and checks:

```bash
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run mypy src scripts
```

## The chain

```text
Website → Crawler → Discovery → Plugin → Provider → Download Manager → Library
```

Each station answers exactly one question, and that is what keeps them
replaceable:

| Station | Package | Question | Network |
| --- | --- | --- | --- |
| Crawl Engine | `maxicrawler.web.engine` | *"Which page comes next?"* | delegates |
| Crawler | `maxicrawler.web` | *"Which URLs does this page contain?"* | required |
| Discovery | `maxicrawler.crawler` | *"Which URLs exist?"* | never |
| Plugin | `maxicrawler.plugins` | *"Can I classify this URL?"* | never |
| Provider | `maxicrawler.providers` | *"What can I do with this resource?"* | allowed |
| Download Manager | `maxicrawler.downloader` | *"How are downloads executed?"* | delegates |
| Library | `maxicrawler.library` | *"How are resources stored?"* | never |

The crawler is the only station that retrieves a document MaxiCrawler was not
given. It knows nothing about providers, downloads, or the library: it fetches a
page, finds the URLs in it, and hands them to the discovery pipeline unchanged.
A link found on a web page is therefore classified by exactly the same plugins
as one found in a local file.

A plugin decides from the URL string alone, so it runs on every URL discovery
finds and can never block. A provider takes the plugin's verdict and asks the
host what the resource actually is. Only commands that say so contact a
provider: `discover` stays entirely offline, `crawl` contacts a web server but
no provider, and `info` and `download` are the ones that reach a host.

**Adding a host means adding a plugin and a provider.** The download manager
and the library do not change — they contain no provider name at all.

## Architecture

The public package lives under `src/maxicrawler/`. Each subpackage owns one
responsibility and communicates through typed, small interfaces.

| Package | Responsibility |
| --- | --- |
| `crawler` | Coordinates the discovery lifecycle and orchestration services. |
| `documents` | Reads local files into a format-independent representation. |
| `extractors` | Converts documents and responses into structured content. |
| `downloader` | Plans, queues, and executes downloads; knows no provider. |
| `library` | Stores downloaded resources and their metadata. |
| `database` | Persistence abstractions and implementations. |
| `plugins` | Plugin protocol, registry, resolution, and discovery. |
| `providers` | Provider protocol, registry, transport, retries, and crypto. |
| `gui` | Optional desktop user interface adapters. |
| `api` | Optional programmatic and HTTP API adapters. |
| `utils` | Shared, dependency-light helpers. |
| `config` | Typed application settings and configuration loading. |

## First implementation sprint

This release adds the application foundations while intentionally leaving
crawling and downloading for a later sprint:

- TOML configuration in `maxicrawler.toml` (created with `maxicrawler init`)
- consistent package logging through `maxicrawler.utils.configure_logging`
- a small SQLite adapter for application metadata
- plugin discovery through the `maxicrawler.plugins` entry-point group
- a Typer CLI with `init`, `config`, and `version` commands

Third-party distributions can register a plugin in `pyproject.toml`:

```toml
[project.entry-points."maxicrawler.plugins"]
example = "my_package.plugin:ExamplePlugin"
```

The plugin object must expose a `name` attribute and a `register()` method.
This is the distribution-level contract; `register()` is where a plugin adds
its `CrawlerPlugin` implementations to a registry (see Sprint 3 below).

## Sprint 2: domain and discovery

Sprint 2 adds the core, immutable domain model and a fully in-memory discovery
pipeline. It has no network access, crawler, downloader, or extractor
implementation.

- `maxicrawler.domain` provides typed `UrlRecord`, `DiscoveryResult`,
  `DownloadTask`, `PluginInfo`, `ScanSession`, and `Statistics` value objects.
- `maxicrawler.events` provides synchronous `EventBus` delivery for scan, URL,
  plugin, and future download lifecycle events.
- `maxicrawler.utils.normalize_url` canonicalizes HTTP(S) URL candidates and
  `DuplicateDetector` tracks them within a session.
- `DiscoveryPipeline` orchestrates local normalization, duplicate detection,
  and lifecycle events only.

## Sprint 3: plugin architecture

Sprint 3 introduces the plugin system that later sprints will extend. It is a
design sprint: there is still no networking, crawling, or downloading. Plugins
classify URLs from their string form alone.

- `CrawlerPlugin` is the public plugin protocol: `metadata`, `can_handle`, and
  `classify`. Plugins are structurally typed, so no base class is required.
- `PluginRegistry` registers, unregisters, discovers, and resolves plugins, and
  publishes `PluginLoaded` / `PluginUnloaded` events.
- `PluginResolver` turns `UrlRecord` objects into immutable `PluginResolution`
  results.
- `GenericPlugin` is the built-in fallback for ordinary HTTP(S) URLs. It
  registers at the lowest priority and never performs network access.
- `DiscoveryPipeline` resolves every unique URL through the registry.

```python
from maxicrawler.events import EventBus
from maxicrawler.crawler import DiscoveryPipeline

pipeline = DiscoveryPipeline(EventBus())
result = pipeline.discover("https://example.test/docs")

assert result.resolution is not None
print(result.resolution.plugin.name)  # generic
print(result.resolution.classification.category)  # generic (UrlCategory.GENERIC)
```

A custom plugin implements the protocol and is registered by priority, so it
outranks the generic fallback:

```python
from maxicrawler.domain import PluginInfo, UrlCategory, UrlClassification, UrlRecord
from maxicrawler.plugins import PluginRegistry, create_default_registry


class ExampleHostPlugin:
    """Handles one specific host."""

    @property
    def metadata(self) -> PluginInfo:
        return PluginInfo(
            name="example-host",
            version="1.0.0",
            module=__name__,
            priority=10,
        )

    def can_handle(self, record: UrlRecord) -> bool:
        return record.normalized_url.startswith("https://example.test/")

    def classify(self, record: UrlRecord) -> UrlClassification:
        return UrlClassification(record, UrlCategory.CONTAINER, "example-host")


registry: PluginRegistry = create_default_registry()
registry.register(ExampleHostPlugin())
```

Plugins depend on the domain and the standard library only. Network access,
persistence, and file-system I/O stay in the infrastructure layer.

## Sprint 4: offline discovery

Sprint 4 delivers the first usable workflow. MaxiCrawler reads local documents,
extracts the URLs they contain, resolves each one through the plugin registry,
and stores the results. There is still **no network access, no crawling, and no
downloading**.

```bash
uv run maxicrawler discover ./docs
```

```text
Documents processed: 4
URLs discovered: 22
Unique URLs: 21
Duplicates removed: 1

Plugin usage:
generic: 21
```

`URLs discovered` counts every URL handed to the pipeline, `Unique URLs` those
seen for the first time, and the two differ by `Duplicates removed`. The plugin
name is the one registered in the plugin registry.

The command accepts a single file or a directory, which is processed
recursively; unsupported files are skipped silently.

```bash
uv run maxicrawler discover ./notes.md            # a single file
uv run maxicrawler discover ./docs --no-persist   # report only, write nothing
uv run maxicrawler discover ./docs --config custom.toml
```

Results are written to the database configured in `maxicrawler.toml`, into a
`scan_sessions` table and a `discovered_urls` table holding the original URL,
its canonical form, the source document, and the responsible plugin.

### Supported input

| Format | Suffixes | How URLs are found |
| --- | --- | --- |
| Plain text | `.txt` | scanned as prose |
| Markdown | `.md` | scanned as prose; inline, autolink and reference syntax all keep the URL literal |
| HTML | `.html`, `.htm` | link attributes (`href`, `src`, …) plus visible text; `script` and `style` are ignored |

Relative links, `mailto:` addresses, and other non-HTTP(S) URLs are skipped
because they cannot be resolved without a base URL.

### Using the workflow from Python

```python
from datetime import UTC, datetime
from pathlib import Path

from maxicrawler.crawler import DiscoveryPipeline, LocalDiscoveryService
from maxicrawler.domain import ScanSession
from maxicrawler.events import EventBus

service = LocalDiscoveryService(DiscoveryPipeline(EventBus()))
session = ScanSession(session_id="local-1", started_at=datetime.now(UTC))
summary = service.run(Path("docs"), session)

print(summary.documents_processed, summary.unique_urls, summary.duplicates_removed)
```

Pass `repository=` to persist the run and `loader=` or `extractor=` to swap in
your own components.

## Sprint 5: the first provider plugin

Sprint 5 proves the plugin architecture carries its weight: Mega share links are
now understood as files and folders with identifiers, instead of being filed
away as generic links. Still **no networking, no API calls, and no downloading**
— the plugin reads the URL string and nothing else.

```bash
uv run maxicrawler discover ./links
```

```text
Documents processed: 2
URLs discovered: 20
Unique URLs: 19
Duplicates removed: 1

Plugin usage:
mega: 13
generic: 6
```

Both URL generations are recognized, on `mega.nz` and the historical
`mega.co.nz`:

| Form | Example | Category |
| --- | --- | --- |
| Modern file | `https://mega.nz/file/<handle>#<key>` | `file` |
| Modern folder | `https://mega.nz/folder/<handle>#<key>` | `container` |
| Modern folder, one entry selected | `…/folder/<handle>#<key>/file/<node>` | `container` |
| Legacy file | `https://mega.nz/#!<handle>!<key>` | `file` |
| Legacy folder | `https://mega.nz/#F!<handle>!<key>` | `container` |
| Legacy folder, one entry selected | `https://mega.nz/#F!<handle>!<key>!<node>` | `container` |

The category describes the **share**; a selected entry is reported through the
`node_handle` and `node_kind` attributes rather than by changing the category.

Each classification carries what the URL stated:

```python
from maxicrawler.domain import UrlRecord
from maxicrawler.plugins import PluginResolver, create_default_registry

url = "https://mega.nz/folder/QwErTyUi#0123456789abcdefghijkl/file/N0d3H4nd"
resolution = PluginResolver(create_default_registry()).resolve(
    UrlRecord(raw_url=url, normalized_url=url)
)

classification = resolution.classification
print(classification.category)  # container
print(classification.attribute("format"))  # modern
print(classification.attribute("handle"))  # QwErTyUi
print(classification.attribute("key"))  # 0123456789abcdefghijkl
print(classification.attribute("node_kind"))  # file
```

`GenericPlugin` remains the fallback and keeps its old job. A Mega URL that is
not a share — the pricing page, a malformed handle — is declined by the Mega
plugin and classified as `generic`.

### URL fragments are now significant

Normalization previously discarded the fragment. A legacy Mega share keeps its
whole identity there, so two unrelated shares compared equal and the second was
silently dropped as a duplicate. Fragments are now preserved verbatim.

As a consequence, `https://example.test/a#intro` and `https://example.test/a`
count as two distinct URLs.

## Sprint 6: the provider layer

Sprint 6 adds the second extension layer. Where a plugin says *what a URL is*,
a provider says *what the resource behind it is* — and for the first time
MaxiCrawler contacts a remote host to find out. It still **does not download**:
the goal of this sprint is metadata only.

```bash
uv run maxicrawler info "https://mega.nz/file/<handle>#<key>"
```

```text
Provider: Mega
Type: File
Name: ubuntu.iso
Size: 5.8 GB
Available: Yes
```

A folder share is described in one request too, including what it holds:

```bash
uv run maxicrawler info "https://mega.nz/folder/<handle>#<key>"
```

```text
Provider: Mega
Type: Folder
Name: Ubuntu Releases
Size: 5.8 GB
Available: Yes
Files: 2
Folders: 1

Contents:
  archive/
  checksums.txt  1.0 MB
  ubuntu.iso     5.8 GB
```

| Option | Effect |
| --- | --- |
| `--offline` | Read the link only; contact no provider |
| `--json` | Print a machine-readable document |
| `--max-entries N` | Limit how many folder entries are listed |
| `--config PATH` | Use a different TOML configuration |

The exit code carries the verdict, so link checking is scriptable:

| Code | Meaning |
| --- | --- |
| `0` | The resource is available |
| `2` | The provider says it is gone, revoked, or blocked |
| `3` | No statement could be obtained (rate limited, quota, or a failure) |

Rate limiting is deliberately **not** reported as "unavailable": a throttled
lookup says nothing about whether the resource still exists.

### `info` downloads nothing

For a Mega file share the provider sends `{"a":"g","p":<handle>}` and leaves the
download flag unset. Mega then reports size and encrypted attributes without
allocating a transfer URL, so no file content moves and no transfer quota is
consumed. A folder share is one `{"a":"f","c":1,"r":1}` request that returns the
whole node tree.

Setting that flag is what allocates a transfer and starts costing the share's
quota, which is why only `download` sets it (see Sprint 7 below).

For an ordinary URL there is no API and no flag: the file describes itself in
its response headers, so an inspection is a single `HEAD` — or a `GET` whose
body is never pulled, for a host that will not answer `HEAD`. Either way no
content moves.

### The decryption key never leaves your machine

A Mega link keeps its key in the URL fragment, which no HTTP client transmits.
MaxiCrawler preserves that property rather than merely inheriting it:

- the key is wrapped in a `ResourceSecret`, readable only through an explicit
  `reveal()` call, and redacted in `repr()` and `str()`;
- `ResourceRef.url` is the share URL with the fragment already removed, so even
  a full reference is safe to print or log;
- decryption happens locally, in a module that has no access to the network;
- the test suite scans every outgoing request, rendering, and log record for
  fragments of the key, and reads the syntax tree to confirm that only the
  provider module unwraps a secret.

Sizes, timestamps, and the structure of a share arrive unencrypted, so a link
published **without** its key is still fully enumerable — only its names stay
hidden:

```text
Provider: Mega
Type: File
Name: unavailable (encrypted)
Size: 5.8 GB
Available: Yes

Names stay encrypted: the link carries no usable decryption key.
```

Reading names needs AES, which is an optional dependency:

```bash
uv sync --extra mega        # or: pip install 'maxicrawler[mega]'
```

### Configuration

```toml
[maxicrawler]
network_timeout = 30.0   # seconds to wait for a provider
network_retries = 3      # attempts before giving up, with exponential backoff
max_entries = 1000       # folder entries listed per inspection
```

### Using a provider from Python

```python
from maxicrawler.domain import UrlRecord
from maxicrawler.plugins import PluginResolver, create_default_registry
from maxicrawler.providers import UrllibTransport, create_default_provider_registry

url = "https://mega.nz/file/QwErTyUi#<key>"
record = UrlRecord(raw_url=url, normalized_url=url)
classification = PluginResolver(create_default_registry()).resolve(record).classification

providers = create_default_provider_registry(
    transport=UrllibTransport(user_agent="MaxiCrawler/0.1.0")
)
provider = providers.resolve(classification)

ref = provider.reference(classification)  # pure: no request is made
inspection = provider.inspect(ref)  # one request, no download

print(inspection.availability)  # available
print(inspection.metadata.name)  # ubuntu.iso
print(inspection.total_size)  # 5800000000
```

`reference()` is deliberately separate from `inspect()`: building a reference is
pure, so it can be done, stored, and compared offline.

### Adding a provider

A new provider implements four members and needs no base class:

```python
class ResourceProvider(Protocol):
    @property
    def metadata(self) -> ProviderInfo: ...
    def supports(self, classification: UrlClassification) -> bool: ...
    def reference(self, classification: UrlClassification) -> ResourceRef: ...
    def inspect(self, ref: ResourceRef) -> ResourceInspection: ...
```

Providers that do not encrypt anything — Pixeldrain, GoFile, MediaFire — simply
leave `ResourceRef.secret` as `None` and never touch the cipher backend. No
change to the protocol, the registry, or the CLI is required to add one.

## Sprint 7: the download manager and the library

Sprint 7 is the first one that actually downloads something. It is deliberately
**not** a sprint about writing a Mega downloader: it introduces a
provider-independent **Download Manager** and a long-lived **Library**, and
Mega is simply the first provider that plugs into them.

```bash
uv run maxicrawler download "https://mega.nz/file/<handle>#<key>"
```

```text
ubuntu.iso ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 5.8/5.8 GB 32.1 MB/s 0:00:00

Downloaded: 1
Skipped: 0
Failed: 0
Stored: 5.8 GB
Library: library
```

### One command, any source

There is one `download` command and one argument, because from the outside the
difference between "a link" and "a file full of links" is not interesting —
both answer the same question with a list of URLs.

```bash
uv run maxicrawler download "https://mega.nz/folder/<handle>#<key>"  # a share
uv run maxicrawler download links.txt                                # a list
uv run maxicrawler download reading-list.md                          # Markdown
uv run maxicrawler download bookmarks.html                           # HTML
uv run maxicrawler download ./documents                              # a folder
```

Documents are read with exactly the same rules as `discover`, so whatever
`discover` finds in a file is what `download` will fetch from it. A folder
share becomes one download per file it holds.

| Option | Effect |
| --- | --- |
| `--output DIR` / `-o DIR` | Store into this library instead of the configured one |
| `--dry-run` | Report what would be downloaded; transfer nothing |
| `--no-progress` | Suppress the progress bars |
| `--max-entries N` | Limit how many folder entries are considered |
| `--config PATH` | Use a different TOML configuration |

The exit code carries the verdict, so downloading is scriptable:

| Code | Meaning |
| --- | --- |
| `0` | Everything the source asked for is in the library |
| `4` | Something was not: a failed transfer, a revoked share, an unhandled link |

A **skipped** download counts as success — the resource is present, it simply
did not have to be fetched again.

### The library layout

Downloads do not land in a flat directory. Every resource gets its own
directory, namespaced by provider:

```text
library/
    library.json                     the store descriptor and its schema version
    mega/
        aabbccdd-1a2b3c4d5e/         one resource
            metadata.json            what it is and where it came from
            content/
                ubuntu.iso           the payload, as the provider named it
            .incomplete/             in-flight files; never a finished download
```

Four properties follow, and each is why a simpler layout was rejected:

- **The file system is the source of truth.** Every entry describes itself, so
  a library survives losing a database, can be moved with `rsync`, and stays
  readable with a text editor.
- **The payload and the metadata cannot collide.** A provider is free to name a
  file `metadata.json`; a separate `content/` makes that harmless.
- **A partial download is never mistaken for a finished one.** Content is
  written under `.incomplete/` and moved into place only once it is whole.
- **An entry is addressed by identity, not by name.** The directory key comes
  from the reference alone, so a renamed remote file — or one read through a
  link that carries no key — still finds the same entry.

The key is `<slug>-<digest>`: a readable stem plus a ten-character digest over
provider, container, and resource. The stem alone would merge `AbCdEfGh` and
`abcdefgh` on a case-insensitive volume, which is the default on Windows and
macOS.

The alternatives that were considered and rejected — mirroring the remote
folder tree, a content-addressed blob store, hash-sharded directories, a SQLite
index as the source of truth, date-based buckets — are recorded with their
reasons in [docs/architecture.md](docs/architecture.md#why-this-layout-and-not-another).

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

It is built to survive: a document written by a **newer** MaxiCrawler is
refused rather than misread, and members this release does not recognise are
preserved verbatim across a round trip, so a future field survives passing
through today's code.

`source_url` never carries a fragment, so a library directory is safe to share,
back up, or paste into an issue — the decryption key is not in it.

### Existing files are skipped

Re-running a download is cheap and safe. The manager asks the library whether
it already holds the resource, and that question needs no network request at
all, so a second run over two hundred already-downloaded links contacts nobody.

```text
Downloaded: 0
Skipped: 200
Failed: 0
```

Both the record and the payload file are checked, so a library whose file was
deleted repairs itself by simply running again. Nothing is ever overwritten
automatically; overwrite options can be added later.

### What this sprint deliberately does not do

**Resume** is not implemented. The architecture is shaped so it can be added
rather than retrofitted: content is already staged under `.incomplete/`, the
stream transport already takes a URL and returns chunks, and the metadata
record already versions itself and preserves unknown members.

**Parallel downloads** are not enabled. The queue is thread-safe and hands out
one job at a time, and the worker holds no state between jobs, so what remains
is a thread pool around the drain loop.

### Nothing in the manager knows about Mega

That is the point of the sprint, and it is checkable by reading: grep
`src/maxicrawler/downloader/` for `mega` and the only hits are none. Where
behaviour differs between hosts, it is asked for through the provider protocol:

```python
class ResourceProvider(Protocol):
    ...

    def download(self, ref: ResourceRef, sink: DownloadSink) -> ContentDescriptor: ...
```

A provider streams bytes into a `DownloadSink` it does not own, so it never
learns where they land; the manager owns the staging file, the digest, and the
progress bar, so it never learns how the bytes were obtained. A provider that
cannot transfer content omits `ProviderCapability.DOWNLOAD` and says so instead
of failing when asked.

### Configuration

```toml
[maxicrawler]
library_path = "library"   # where downloads are stored; --output overrides it
network_timeout = 30.0
network_retries = 3
max_entries = 1000
```

### Using the download manager from Python

```python
from maxicrawler.downloader import DownloadManager
from maxicrawler.library import Library
from maxicrawler.providers import (
    UrllibStreamTransport,
    UrllibTransport,
    create_default_provider_registry,
)

providers = create_default_provider_registry(
    transport=UrllibTransport(user_agent="MaxiCrawler/0.1.0"),
    stream=UrllibStreamTransport(user_agent="MaxiCrawler/0.1.0"),
)
manager = DownloadManager(providers, Library("library"))

report = manager.download("links.txt")

print(len(report.completed), len(report.skipped), len(report.failed))
for outcome in report.completed:
    print(outcome.label, outcome.path)
```

`plan()` and `run()` are separate, so a caller can inspect what would happen
before it happens:

```python
plan = manager.plan("https://mega.nz/folder/<handle>#<key>")
print(len(plan.jobs), plan.total_size)
report = manager.run(plan)
```

Without a `stream=` transport the registry produces inspection-only providers
for every host with an API behind it. `info` is composed that way, and also
gets a `files=` transport so it can describe an ordinary URL — see
[Direct downloads](#direct-downloads-the-file-at-the-url). What keeps that
command from downloading is that it asks `inspect` and never `download`; an
inspection is one `HEAD`.

## Sprint 8: the web crawler

Sprint 8 adds the first station of the chain. `crawl` fetches **one** web page,
reads the links out of it, and runs them through the discovery pipeline that
already exists:

```bash
maxicrawler crawl https://example.org
```

```text
Fetched:   https://example.org/
Status:    200 text/html (utf-8, 1256 bytes)
Title:     Example Domain

Links found: 42
  anchor: 30
  image: 6
  script: 3
  stylesheet: 2
  meta refresh: 1
Skipped (not HTTP(S)): 5

Documents processed: 1
URLs discovered: 37
Unique URLs: 30
Duplicates removed: 7

Plugin usage:
generic: 28
mega: 2
```

The last two blocks are the same renderer `discover` uses, because they are the
same numbers from the same pipeline.

### One page, and only one

The crawler fetches the page you name and **follows nothing**. Links are
reported, never visited. There is no `--depth`, no `--recursive`, and no queue,
because this sprint is about discovering what is on a page rather than about
walking a site.

That is a scope decision, not a limitation of the design: `crawl()` returns an
immutable result and holds no state about which URL comes next, so recursion is
a loop *around* it rather than a change inside it. The extension points are
described in [docs/architecture.md](docs/architecture.md#how-this-extends-to-recursion).

### What it reads

| Element | Taken from |
| --- | --- |
| `<a href>`, `<area href>` | the link target |
| `<img src>` | the image source |
| `<script src>` | the script source |
| `<link href>` | any `rel`, including `stylesheet` and `canonical` |
| `<iframe src>` | the frame source |
| `<meta http-equiv="refresh">` | the `url=` inside `content` |
| plain text | bare URLs written in prose, via `--prose` (the default) |

The last row is there because a share link on a forum page is usually written
out rather than linked. It uses the same rule that finds a URL in a Markdown
file — never a second scanner — and ignores anything inside `<script>` or
`<style>`. Turn it off with `--no-prose`.

Relative URLs are resolved against the page, `<base href>` is respected, and a
page reached through a redirect resolves against the URL that **answered**, not
the one requested. `crawl --json` reports both.

URL fragments are kept. That is not cosmetic: a legacy Mega share carries its
whole handle and decryption key in the fragment, so a crawler that strips
fragments would silently lose every one of them.

### What it deliberately does not do

- **No JavaScript.** A page that builds its links in the browser will show
  fewer here than a reader sees.
- **No cookies, no login, no forms, no headless browser.** Static HTML only.
- **No downloads.** `crawl` contacts a web server; it contacts no provider and
  writes no file into the library.
- **No robots.txt — at the time.** Fetching one page named by its operator is
  what a browser does when the same person types the same address. Sprint 13
  changed that answer along with the premise: a crawl follows links now, and
  robots.txt is obeyed by default.

### Every fetch is bounded

The page belongs to a stranger, so each of these is enforced rather than hoped
for: only `http` and `https` are opened, on the first request and on every
redirect hop; redirects are capped and recorded; the content type is checked
from the headers *before* the body is read, so a video answered to a page
request costs one round trip instead of a download; and the size limit applies
both to what arrives and to what a compressed body expands to, so a small
archive that inflates to gigabytes is refused like a large one.

Errors say which of those happened, and the exit code separates them:

| Exit code | Meaning |
| --- | --- |
| 0 | the page was fetched and read |
| 5 | it could not be retrieved — refused, unreachable, too large, too many redirects |
| 6 | something answered, but it was not a page |

### Configuration

```toml
[maxicrawler]
user_agent = "MaxiCrawler/0.1.0"
network_timeout = 30.0
max_page_bytes = 8388608
max_redirects = 5
max_links = 10000
```

### Using the crawler from Python

```python
from maxicrawler.crawler import DiscoveryPipeline
from maxicrawler.events import EventBus
from maxicrawler.web import UrllibPageFetcher, WebDiscoveryService

service = WebDiscoveryService(
    DiscoveryPipeline(EventBus()),
    fetcher=UrllibPageFetcher(user_agent="MaxiCrawler/0.1.0"),
)
result = service.crawl("https://example.org", session)

print(result.requested_url, "->", result.final_url)
for link in result.links:
    print(link.kind, link.resolved_url)
print(result.summary.unique_urls)
```

`CrawlResult` is an immutable value and the service takes no terminal, so the
same call serves a script, a future API, and a future GUI. A long crawl needs
no polling either: the pipeline already publishes `ScanStarted`,
`UrlDiscovered`, and `ScanFinished` on the event bus.

## Sprint 9: the crawl engine

Sprint 9 makes the crawler recursive, and does it entirely by addition — not one
line of the fetcher, the parser or the resolver changed.

```bash
maxicrawler crawl https://example.org --depth 2
```

```text
Crawl:     https://example.org/  (depth 2, any domain, max 50 pages)
Finished:  completed in 6.2s

Pages visited: 14
  200  d0  https://example.org/
  200  d1  https://example.org/docs/
  404  d2  https://example.org/docs/old  (failed)
  ...
Pages failed: 1
Pages skipped: 128
  out of scope: 96
  already seen: 30
  too deep: 2

Links found: 412
  anchor: 380
  image: 25
  plain text: 7

Documents processed: 14
URLs discovered: 412
Unique URLs: 284
Duplicates removed: 128

Plugin usage:
generic: 281
mega: 3
```

The last block is the renderer `discover` uses, because they are the same
numbers from the same pipeline. `Documents processed` and `Pages visited` are
one number seen twice, not two numbers to add up.

### Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--depth`, `-d` | `0` | link distance from the start page; 0 fetches it alone |
| `--same-domain` / `--any-domain` | any domain | stay on the starting host |
| `--include-subdomains` | off | treat `docs.example.org` as the same domain |
| `--below-seed` / `--anywhere` | anywhere | stay under the path the start URL names |
| `--max-pages` | `50` | stop after this many pages |
| `--json` | off | the machine-readable report |
| `--prose` / `--no-prose` | prose | also read URLs written as plain text |
| `--persist` / `--no-persist` | persist | store the summary and the URLs |

Every default except the last two is configurable — `crawl_depth`,
`crawl_max_pages`, `crawl_same_domain` and `crawl_below_seed` in
`maxicrawler.toml`.

### Staying under one path

`--same-domain` asks about the host, and on a site that gives each section its
own path that is not the question. All of `boards.example.org/hr/`,
`/g/` and `/biz/` are one domain, so the domain rule walks the lot.

`--below-seed` is the narrower rule:

```bash
maxicrawler crawl https://boards.example.org/hr/ --depth 3 --below-seed
```

That reaches `/hr/` and everything under it, and nothing else on that host.

Three things worth knowing about it:

- **It carries the host itself**, so it replaces `--same-domain` rather than
  needing it. Both flags together is the narrow rule, not a contradiction.
- **Subdomains are always outside it.** `docs.example.org/hr/` is a different
  site, not a place below `example.org/hr/`, so `--include-subdomains` has no
  effect here.
- **A start URL whose last segment looks like a file names its directory**, so
  `/docs/guide.html` covers `/docs/`. Add a trailing slash when you mean the
  path literally: `/docs/v1.0/` is a directory, `/docs/v1.0` is read as a file
  and covers `/docs/`.

It changes what is *fetched*. Links pointing out of scope are still discovered
and still appear in the report — they are just not followed, which is where the
volume comes from: without it, crawling one board pulls in every other board's
pages and every link on them.

### Links off-site are followed by default

That is a decision, not an oversight. MaxiCrawler serves two workflows equally:

- **crawling one website**, where `--same-domain` is what you want;
- **hunting for share links**, where the interesting URLs are on Mega,
  Pixeldrain, GoFile or Dropbox *by definition* — restricting by default would
  quietly break it.

`--depth` and `--max-pages` are what keep a crawl finite, and both are on by
default. A crawl with no `--depth` still fetches exactly one page.

### What it will not do twice

- The same page linked from forty pages is fetched once.
- `page#intro` and `page#setup` are one page. URL fragments are kept everywhere
  else, because a legacy Mega share carries its key in one — but they never
  make two pages out of one.
- A page reached through a redirect is not fetched again through a link
  pointing straight at the redirect's target.
- A cycle terminates, and so does a page that links to itself.

### Files are documented, not fetched

A link to a PDF, a ZIP, an MP3 or an image is **discovered, classified, counted
and stored** like every other URL — that is the point of the tool. It is simply
never *requested*, because a file cannot answer with a page and asking costs a
round trip to be told what the URL already said.

Three filters do it, and each only handles what the cheaper one before it
could not: the kind of link it was written as, the extension its path ends in,
and — for a URL that gives nothing away, like `/download?id=7` — the content
type of the reply. All three report the same reason: `not a page link`.

That last case costs one request, so the page ceiling counts **requests
issued**, not pages read; `Pages attempted` appears in the report when the two
differ, and is then the line that explains why a crawl stopped. A wrong content
type is not counted under `Pages failed`: being told "this is not a page" is an
answer, not a fault.

A URL **you** name is always attempted. An explicit instruction outranks a
heuristic, so `maxicrawler crawl https://example.org/sheet.pdf` tells you what
actually came back.

Not fetching them during a crawl has never meant not fetching them at all:
every one of these links can be downloaded from the report, and since the
direct provider that is true whatever host they sit on. See
[Direct downloads](#direct-downloads-the-file-at-the-url).

`<link rel="canonical">` is recorded and reported but never used to skip a URL.
A page can declare a canonical it does not equal, and skipping a URL that was
never fetched loses every link on it.

### Stopping

Ctrl-C stops the crawl and still prints the full report of what was done; the
exit code is 7. Reaching `--max-pages` is not a failure — the crawl did what it
was told — so that exits 0 and says so in words.

| Exit code | Meaning |
| --- | --- |
| 0 | the crawl ran to an end, or to a limit it was given |
| 5 | the starting page could not be retrieved, or was refused |
| 6 | the starting page was not a page |
| 7 | the crawl was interrupted |

### Still not done, on purpose

- **robots.txt** — the stakes rose here, with a crawl fetching many pages rather
  than one, and Sprint 13 answered it: the `CrawlPolicy` seam took a
  `RobotsPolicy` without the engine changing.
- **No delay between requests** — the `ThrottledFetcher` seam was documented and
  empty at this point, and filled in Sprint 13.
- **No parallelism, no downloads, no JavaScript, no cookies, no login.** The
  crawler discovers; downloading stays a separate pipeline.

### Using the engine from Python

```python
from datetime import UTC, datetime

from maxicrawler.crawler import DiscoveryPipeline
from maxicrawler.events import EventBus, PageCrawled
from maxicrawler.web import UrllibPageFetcher, WebDiscoveryService
from maxicrawler.web.engine import CrawlEngine
from maxicrawler.web.session import CrawlOptions, CrawlSession

bus = EventBus()
bus.subscribe(PageCrawled, lambda event: print(event.depth, event.url))

service = WebDiscoveryService(
    DiscoveryPipeline(bus),
    fetcher=UrllibPageFetcher(user_agent="MaxiCrawler/0.1.0"),
)
engine = CrawlEngine(service, event_bus=bus)
report = engine.run(
    CrawlSession(
        session_id="demo",
        seed_url="https://example.org",
        started_at=datetime.now(UTC),
        options=CrawlOptions(max_depth=2, same_domain=True),
    )
)

print(report.state, report.pages_visited, report.links_discovered)
```

`engine.control.request_stop()` stops a running crawl from another thread — the
same path Ctrl-C takes, and the one a future Stop button will use. `CrawlReport`
is immutable and takes no terminal, so the CLI, a future API and a future GUI
all render the same value.

## Sprint 10: the web interface

Sprint 10 adds a browser interface, and adds no crawling behaviour at all. It is
a second *client*, not a second *implementation*: every crawl it starts is built
by the same service the command line calls, and every number it shows comes from
the same report.

```bash
maxicrawler serve
```

```text
MaxiCrawler is listening on http://127.0.0.1:8000/
```

The command line stays whole. It is the client for automation, scripting and
tests; the web interface is the one meant for looking at, and is intended to
become the primary one.

### The four sections, from the first page

| Section | What it does today |
| --- | --- |
| **Dashboard** | Start a crawl, and see the recent ones. |
| **Crawls** | Every crawl this installation has run, live or stored, and one page per crawl. |
| **Library** | Named, and empty. Listing it will go through a service, the way crawling does. (Sprints 11 and 12 filled it in.) |
| **Settings** | The configuration as it was read, and which file it came from. Read-only. |

Naming all four from the beginning is deliberate. Two of them do very little
yet, and saying so is cheaper than rearranging every page around them later.

(A fifth, **Downloads**, joined them in Sprint 15 — not from the beginning,
because until there was a queue it would have been a heading over a single
link. That is the exception the rule above is about: a section earns its place
when there is a set to show, not when there is one thing.)

### A crawl you can watch

Starting a crawl redirects to its page immediately; the crawl itself runs on a
worker thread, so the server keeps answering while it works — which is what
`/health` is there to prove.

The page then updates itself over server-sent events: pages visited, pages
failed, links found, the URL it is on, and how far it has got through its page
budget. **It works with JavaScript switched off.**
Every page is complete from the server, the stream only replaces numbers that
are already there, and reloading asks for the same numbers again.

Stopping is the same request Ctrl-C makes, and the report is the same report:

```bash
curl http://127.0.0.1:8000/crawls/<job-id>.json
```

That is the document `crawl --json` prints, from the same function —
`crawl_document` in `maxicrawler.app`, which neither client has its own version
of.

### After a restart

Jobs live in memory; crawls live in the database. Restart the server and the
crawl list still shows everything — including everything the CLI ever ran — with
each page read back from storage.

A crawl that was running when the process ended is called `abandoned` rather
than left looking live: a row is only "running" when the database and the
registry agree. And what storage does not hold, the page says it does not hold.
The page table and the skip reasons are not persisted yet, so a recorded crawl
reports them as *not recorded* instead of drawing an empty table that reads as a
zero.

### Where it listens, and why that is a flag

The interface has **no authentication**, and anyone who can reach it can start a
crawl — an outbound request made from your machine, charged to your address. On
`127.0.0.1` that means whoever is already logged in here. Anywhere else it means
something different, so anywhere else has to be asked for:

```bash
maxicrawler serve --host 0.0.0.0 --allow-remote
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--host` | `127.0.0.1` | address to bind |
| `--port` | `8000` | port to bind |
| `--config` | `maxicrawler.toml` | the configuration the server runs under |
| `--allow-remote` | off | permit an address other machines can reach |

Without the flag, a non-loopback address is refused with the reason and the
remedy. A hostname counts as remote without consulting a resolver: a name can
point anywhere, and can start pointing somewhere else tomorrow.

This is not security and is not offered as any — a flag stops nobody
determined. It is the difference between exposing a service and exposing one by
accident, which is the failure that actually happens.

### No build system

Starlette and Jinja2 render the pages. There is no React, no bundler, no npm and
no TypeScript; the browser loads one stylesheet and one hand-written script,
both served out of the package. Nothing is fetched from another host, so the
interface renders on a machine with no route to the internet.

That follows from what it is: an operator's console — tables, counters, a
progress line — closer to Grafana or Proxmox than to an application. A build
system would add a toolchain, a lockfile and a second language to a project
whose point is that its parts stay separable.

### The boundary, and how it is kept

```text
maxicrawler.cli ─┐
                 ├─→ maxicrawler.app ─→ web / crawler / database / plugins
maxicrawler.api ─┘
```

`maxicrawler.app` is the composition root — the one package allowed to know
`config`, `database`, `web` and `crawler` at once. It came first: the crawl
graph moved out of the CLI and the CLI was changed to call it *before* the web
package existed, so there was never a second version to keep in step.

`tests/test_api_boundaries.py` reads the import graph rather than trusting the
prose. The web package imports no provider, no downloader and no library; it
imports nothing the service assembles, so it cannot grow a second crawler by
accident; and no core package imports it back. The one exception is the command
line, because `serve` lives there — and on import it reaches for the module that
names the missing extra, and nothing else.

The interface is an optional extra:

```bash
pip install "maxicrawler[web]"
```

Without it every command still runs. `serve` explains what is missing in a
sentence and exits 8.

### Still not done, on purpose

- **No authentication.** Hence loopback by default. A reverse proxy that
  authenticates is the answer until the interface has accounts of its own.
- **No pause or resume.** Stop is a stop; a stopped crawl is finished.
- **No downloads from the browser.** Downloading goes through a service in
  `maxicrawler.app` first, or it becomes a second implementation. *(Sprint 11
  built that service, and then the button.)*
- **No library listing**, for the same reason.
- **htmx is not vendored.** Its licence (0BSD) is checked and the routes already
  render standalone fragments, which is the expensive half. It is worth adding
  the day filtering and sorting need it.

## Sprint 11: crawl, report, download, library

Sprint 11 joins the two halves of the chain. In a browser you can now crawl a
page, look at what it found, press Download beside a Mega link, watch the bytes
arrive, and find the file in the library afterwards. That is the whole sprint:
one link, one click, one file.

```text
Crawl → Report → Download → Library
```

Nothing about *how* downloads work changed. `DownloadManager`, the planner, the
queue, the worker, the sink and the library are the same code the command line
has used since Sprint 7.

### One service, both clients

What was missing was the composition point. The `download` command assembled its
own provider registry, library and manager — the same arrangement `crawl` had
before Sprint 10, and the same way two clients quietly become two
implementations. So `DownloadService` was extracted into `maxicrawler.app`, the
command line was changed to use it, and only then did the browser learn to
download:

```text
Browser  ─┐
          ├─→ DownloadService ─→ DownloadManager ─→ Provider ─→ Library
CLI      ─┘
```

The service reports in plain values — `DownloadProgress` while a transfer runs,
`DownloadSummary` when it is over, `LibraryItem` for what is stored — so the web
layer shows a download without importing `downloader`, `providers` or `library`
at all. `tests/test_api_boundaries.py` reads the import graph and fails the
build if that stops being true.

### One download at a time, on purpose

There is no queue, no batch and no scheduler. Ask for a second download while
one is running and the interface says so and names the one that is running:

```text
Download not started
a download is already running; this interface runs one at a time
```

A queue needs a policy for ordering, cancelling, resuming and surviving a
restart. None of that is worth inventing before a single download works end to
end, and everything the refusal costs is one click later.

### What the button does, and does not, put in a URL

A Mega share carries its decryption key in the URL fragment, which a browser
never transmits as part of a URL — and does transmit as a form field. So the
Download button is a small form, the link travels in the request body, and
everything downstream holds the fragment-free URL: the run, every snapshot,
every rendered page and every event frame. The key reaches the provider and
nothing else.

The button appears only beside links this installation could actually fetch,
which is asked of the URL string alone: a plugin classifies it, a provider
claims the classification, and the provider says whether it was composed with
everything a transfer needs. No request is made while a report renders.

### A download you can watch

The same machinery a crawl uses, written once and used twice: the transfer runs
on a worker thread, the page arrives complete from the server, and an
`EventSource` replaces the numbers already in it. With scripting off the page
still works and stops updating by itself.

```text
Downloading…
████████████████░░░░░░
1.3 MB of 2.8 MB · 46%
```

A transfer whose size nobody stated gets an indeterminate bar and an honest
counter rather than one stuck at zero. For a plain file link the size is known
before the first byte, because a single deliberate download can afford to ask:
the plan is made with `inspect_files=True`, one request that buys the file's
name and size. A run over a document full of links still does not — two hundred
links must not become two hundred extra requests.

### Only URLs, never paths

The resolver underneath a download reads a file, or a whole directory of
documents, for the links inside. That is right for a command line and would be a
way to make a server read its own disk on somebody else's click, so
`DownloadService.require_url` refuses anything that is not an absolute HTTP(S)
URL — before a worker thread is started, so a bad link is a message rather than
a run that exists only to have failed.

### The library, in the browser

Five columns, read through the service: provider, name, size, when it was
downloaded, and where it is. No search, no filters, no preview. Each row comes
from that entry's own `metadata.json`, because the file system is the library's
source of truth (ADR-010); a damaged entry is skipped rather than allowed to
empty the page.

### Still not done, on purpose

- **No Stop for a download.** A crawl checks between pages; a transfer has no
  such seam yet, so `serve` leaves a running one alone when it shuts down. That
  is safe rather than merely tolerated: content becomes visible only once it is
  whole, so an abandoned transfer leaves no half file behind.
- **No queue, no parallel downloads, no scheduler.**
- **A download's own page dies with the process.** The library is what survives,
  which is where a finished download actually lives.
- **Still only Mega.** A second provider needs a plugin and a provider, and not
  one line of what this sprint added.

## Sprint 12: the library, and looking at what is in it

Sprint 11 got a file into the library. Sprint 12 is about not having to leave
MaxiCrawler afterwards: search it, sort it, open one file's page, and look at the
file itself in the browser.

### The library became a listing

```text
Library                                          7 of 412
─────────────────────────────────────────────────────────
 Search [ jump            ]  Provider [ mega ▾ ]  Status [ any ▾ ]
─────────────────────────────────────────────────────────
 PROVIDER   NAME        SIZE      DOWNLOADED ▾   STATUS
 mega       Jump.pdf    1.3 MB    2026-08-10     completed
```

Search matches the name, the stored file name and the link it came from — that
last one is how you find something again when you remember the URL and not the
title. Every column heading sorts, the arrow says which way, and a footer walks
the pages fifty at a time.

All of it is a GET form and plain links, so every view has its own URL, the
browser's back button is the navigation, and the page works with scripting off.
**No htmx**: on a loopback server a round trip costs less than the vendored file
would, and the routes already render standalone fragments if that ever changes.

A **failed** download is a row too, with its reason on its page. "Where did my
failed download go" is exactly the question somebody brings to a library.

### A page per file

```text
Library / Jump.pdf

[completed]  Jump.pdf                                  Download

  Provider      mega
  Size          1.3 MB
  Downloaded    2026-08-10 14:30
  Original URL  https://mega.nz/file/AaBbCcDd
  SHA-256       9f86d081884c7d65…

  ┌──────────────────────────────────────────────┐
  │  (the file, shown by the browser)            │
  └──────────────────────────────────────────────┘

  Path  [ library/mega/handle00-b16ff6eee4/content/Jump.pdf ]  Copy
```

There is no "open in the file manager" button, because there cannot be one that
works: a `file://` link from an `http://` page is blocked by every browser, and
having the server run `explorer` on an HTTP request would mean a web page
launching a local program. The path is a field that selects on click, with a copy
button that appears only when scripting can make it work.

### The viewer renders nothing

PDF, images, text, Markdown and stored HTML are shown by the browser. MaxiCrawler
states a content type and hands the bytes over; there is no PDF renderer, no
Markdown converter and nothing to keep up to date.

| What | Served as | Shown in |
| --- | --- | --- |
| `.pdf` | `application/pdf` | a frame |
| `.png .jpg .gif .webp .bmp .ico .avif` | its own type | an `<img>` |
| `.svg` | `image/svg+xml` | an `<img>`, never a frame |
| `.txt .log .csv .json .xml .md …` | `text/plain; charset=utf-8` | a frame |
| `.html` | `text/html; charset=utf-8` | a sandboxed frame |
| anything else | `application/octet-stream` | not shown; download instead |

**Markdown is shown as its source**, and that is not a shortcut. No browser
renders Markdown, `text/markdown` makes Chrome download the file, and converting
it would mean rendering it here — which is the one thing this viewer does not do.

The table is explicit rather than asking `mimetypes`, which reads the Windows
registry: the type of a `.webp` would differ between a developer's machine and
the CI meant to check it, and a content type is what decides whether a browser
executes something.

### The part that needed care

A downloaded HTML page or SVG served inline runs in **this application's origin**,
and the interface has no authentication (ADR-025). Such a page could read the
settings page, start a crawl, start a download.

So HTML and SVG are served with `Content-Security-Policy: sandbox` and shown in a
sandboxed frame, which makes the browser treat them as their own opaque origin —
verified in a browser, not assumed: from the page around it, the framed document
is unreachable.

The other types are served *without* that policy, and that is a measurement
rather than an omission. The first version applied it to everything, on the sound
ground that "which types are dangerous" is a question answered wrongly once and
then kept. Chrome then refuses to render a PDF at all — `ERR_BLOCKED_BY_CLIENT`,
because the directive blocks the plugin its viewer is. A PDF, an image and plain
text cannot execute script in our origin, so the policy would have cost the whole
feature and bought nothing. See ADR-027.

A key that arrives in a URL is checked before it becomes a path segment, resolved,
and refused if it leaves the library root — which a symbolic link inside the
library would. A file above `max_view_bytes` (32 MiB, configurable) is offered for
download rather than shown, because a browser handed a 400 MB text file stops
answering.

### Two services over one store

`DownloadService` writes into the library; `LibraryService` reads it. Searching,
sorting and paging live in the second one, in `maxicrawler.app`, so a browser and
a future `library list` command cannot disagree about what "sorted by name"
means. The web layer still imports neither `library` nor `downloader` nor
`providers`, and the import graph is read by a test that says so.

### Still not done, on purpose

- **No index.** Every listing reads one small metadata document per stored
  resource — about 0.3 seconds for two thousand entries warm, and roughly sixteen
  the first time a virus scanner sees them. An mtime-keyed cache would fix it, and
  ADR-010 already permits one as a cache; a library of a few dozen entries does
  not notice, and a cache nobody needs goes stale.
- **No thumbnails, no previews in the table, no text extraction.** All three mean
  reading files in order to render them.
- **No renaming, no deleting, no tags.** The library is browsable, not yet
  editable.

## Sprint 13: responsible and safe crawling

Everything so far made MaxiCrawler fetch more: many pages instead of one, from a
browser instead of a terminal. This sprint is about what a program that fetches
a lot owes the machines it fetches from — and what a program that takes a URL
from a browser owes the machine it runs on.

Four things, and none of them is a special case anywhere in the engine.

### robots.txt is obeyed, by default

```text
$ maxicrawler crawl https://example.org/ --depth 2 --same-domain

Crawl:     https://example.org/  (depth 2, same domain, max 50 pages)
Finished:  completed in 3.1s

Pages visited: 12
Skipped:
  disallowed by robots.txt   7
  already seen               4
```

The refusal has a name of its own. *"Outside my scope"* is a choice you made;
*"disallowed by robots.txt"* is one the site made, and a report that merged them
could not tell a narrow crawl from a refused one.

Wildcards (`*`), the end anchor (`$`), longest-match precedence with `Allow`
breaking ties, several user-agent groups and `Crawl-delay` are all honoured,
because the matching is [Protego](https://github.com/scrapy/protego)'s. The
standard library's `urllib.robotparser` compares paths with `startswith`, so
`Disallow: /*.pdf$` matches nothing there — it would let us fetch what a site
forbade while believing we obeyed. ADR-029 records the evaluation, including the
one thing Protego does not do (strip a byte-order mark) and where we do it.

| The host answers | MaxiCrawler | Why |
| --- | --- | --- |
| a robots.txt | obeys it | |
| 404, 403, 410 | crawls | RFC 9309: unavailable means you may |
| 500, a timeout | **does not crawl** | not knowing what a site permits is not permission |
| HTML, or 3 MB of it | crawls | content we declined to read is not a server saying no |

It is read **once per host**, and only for URLs actually about to be fetched. At
the moment a link is *found* it would cost one request per domain a page
mentions: one page linking to three hundred sites would spend three hundred
requests before crawling fifty pages.

Turning it off is one flag — `--ignore-robots`, a checkbox on the crawl form, or
`respect_robots = false`. A safe default nobody can find is a default people work
around instead of with. And every crawl says which it was — `depth 2 · same
domain · max 50 pages · robots.txt obeyed` — from the stored column rather than
from today's configuration, because a setting that has changed since cannot
answer that later. Either way, never by silence: a line that spoke up only when
robots.txt was ignored would answer the question only for somebody who already
knew the default.

**Downloads are not affected.** No provider consults robots.txt: a download is an
explicit act on a resource you named, and file hosts disallow crawlers as a
matter of course.

### Politeness, without a delay nobody asked for

`crawl_delay` defaults to **0.0**. A host that wants to be crawled slowly says so
in its robots.txt, and that *is* obeyed — up to `max_crawl_delay`, because one
line reading `Crawl-delay: 86400` would otherwise freeze a crawl for a day.

Waiting happens in a `ThrottledFetcher` wrapping the fetcher, never in the
engine and never in the frontier. *"May I fetch this?"* and *"may I fetch it
yet?"* are different questions, and there is no `sleep` anywhere above that one
file. A stop during a delay returns at once rather than holding a shutdown open.

Both fetchers — pages and robots.txt — share one schedule, so the robots request
is spaced like every other request without the file having to describe its own
retrieval.

### It will not crawl your own machine

```text
$ maxicrawler crawl http://localhost:9200/
Error: nothing to crawl: the seed was private network
```

Loopback, RFC 1918, link-local, carrier-grade NAT, unique local addresses, the
names that mean a local network (`localhost`, `*.local`, `*.internal`,
`*.home.arpa`), and every cloud metadata service are refused — in the URL, in
what a name resolves to, **and on every redirect hop**. That last one is where
this actually matters: a public URL answering `302 Location:
http://169.254.169.254/` walks straight past a check made once at the start.

`127.1`, `0x7f.0.0.1` and `2130706433` are refused too. Python's `ipaddress`
calls them malformed and the C resolver every socket goes through calls them
loopback, so a guard that trusted only the strict reading would permit the fetch
and the connection would go to loopback anyway.

Crawling your own network stays possible:

```toml
[maxicrawler]
allow_private_networks = true                    # the whole of it
private_network_allowlist = ["192.168.1.20"]     # or one machine
```

`--allow-private` does the same for one run. Neither opens a cloud metadata
service: opening an intranet to a crawler is not volunteering an instance
credential, and those are one setting only by accident.

**What this does not close is DNS rebinding** — between our lookup and the
connection's there is a second lookup. It raises the cost of reaching an
internal address; it does not make it impossible, and ADR-031 says so rather
than implying otherwise.

### A download can be stopped

A crawl has had a Stop button since Sprint 9. A transfer had none: it checked
nothing between the first byte and the last, so stopping meant waiting for the
file, and `serve` shutting down held on until it was done.

The button is now beside the crawl's, and the stop takes effect within one
chunk. Nothing half-written is left behind — the staging directory already
guaranteed that for a transfer that broke, and this takes exactly the same path.

A stopped download is not called a failure anywhere you read it, and writes no
metadata record: a record saying "failed" would turn your own decision into a
fault you later have to explain, and would count an attempt nobody made.

### Configuration

```toml
[maxicrawler]
respect_robots         = true    # obey each host's robots.txt
robots_user_agent      = ""      # empty: derive the token from user_agent
robots_timeout         = 10.0    # shorter than network_timeout: this is overhead
robots_deny_on_error   = true    # a host we could not reach forbids everything

crawl_delay            = 0.0     # no delay of our own
respect_crawl_delay    = true    # a host's own Crawl-delay is obeyed
max_crawl_delay        = 30.0    # ...up to here

allow_private_networks    = false
private_network_allowlist = []
```

Every default is the safe one.

### Still not done, on purpose

- **DNS rebinding**, as above: closing it means pinning the checked address onto
  the connection that is opened, which is a change to how sockets are made.
- **One politeness schedule per crawl**, not per process. Exactly right while
  `serve` runs one crawl worker; wrong the moment it runs two.
- **Sitemaps**, which robots.txt already tells us about and nothing yet reads.
- **A download queue.** Stopping one download is not scheduling several, and
  that is still a separate subject with an order, a resume and a restart in it.

## Sprint 15: workflow and productivity

The jump from Sprint 13 is not a gap in the record: the sprint numbering and
the milestone numbering came one apart after 0.13, and nothing between them was
large enough to be a milestone of its own. This sprint is milestone 0.14.

Everything so far made MaxiCrawler *capable*: it crawls politely, downloads
provider-independently, and stores what it fetched in a library you can search.
What it was not yet was *quick to use*. A crawl of a link directory produced a
table of four thousand rows with no way to narrow it, and every download was one
click on one row followed by waiting for it to finish before the next one could
start.

This sprint is about the distance between "MaxiCrawler can do that" and "I did
that". Five things, and the last one is what the first four were for.

### A report you can navigate

The link table searches, filters, sorts, pages, and lets you hide columns you
are not reading — all rendered by the server, all in the URL, so any view of it
can be bookmarked and shared.

Filtering is by plugin, by category, by what the URL points at, and by whether
this installation could fetch it at all. The chips carry counts, because how
many of a thing there are is most of what decides whether you want only those:

```text
Plugin      mega 1,291    generic 2,684    (unresolved) 25
Type        archive 902   document 411     video 87
```

Two details worth naming. The counts are over the **whole crawl** rather than
over the matches, the same way the library lists its providers — choosing one
filter must never remove the entry you would use to choose a different one. And
a page number past the end is clamped rather than refused, so a bookmark from
before a re-crawl lands on the last page instead of an error.

The table of pages the crawl *reached* got the same treatment, with its own
parameters, so filtering one never quietly discards the filter on the other.

### A link is classified by what it points at

`document`, `image`, `archive`, `video`, `audio`, `page`, `unknown` — decided
from the URL alone, from an explicit table rather than from `mimetypes`, which
reads the Windows registry and would make the same crawl classify differently on
two machines.

This is a different question from which plugin claimed a URL. A host-specific
plugin can classify a link whose provider cannot transfer anything, and "show me
the archives" is a question nobody could ask before.

### A download queue

ADR-026 said "one at a time, and no queue", and gave the reason: a queue needs a
policy for ordering, cancelling, resuming and surviving a restart, and none of
it was worth inventing before one download worked end to end. It has worked for
several sprints. ADR-033 answers three of those four questions and refuses the
fourth in writing.

Requests are drained in the order they arrived by a single worker. You can move
one up, down or to the front; pause the queue; and remove something waiting or
stop something running with the same button, because they are one intention.
Retry queues a *new* request rather than resetting the old one, so what happened
the first time stays readable.

Two things it deliberately does not do. It does not resume a *file* — that needs
range requests and a stored byte offset, and the word is overloaded enough to be
worth saying plainly. And it does not survive a restart, which it could not do
honestly before resume exists: a restored queue could only offer to start the
same files again from zero.

One worker is a politeness decision rather than a limit. The queue is guarded
throughout and the worker holds no state between requests, so a second thread on
the same drain loop needs no other change — what stops it is that "how many
transfers may one host face at once" is the kind of question robots.txt answers
for crawling, and this sprint is about a person's workflow rather than a host's
patience.

### Downloads became a section

`/downloads` shows the whole queue: what is running, what is waiting, and what
became of the rest, with the counters that answer "how much is left".

It has no event stream of its own. It embeds the running transfer's stream,
because the moment that transfer ends is exactly the moment the next one starts
and everything else on the page changes. A queue nobody is draining has nothing
to send. What the page does with that event, and what it says above the tables,
is where the next milestone went.

Reordering is three buttons rather than drag and drop. That would be a
JavaScript dependency for the last five percent of a control the buttons already
give, on a page that otherwise needs none — and it would leave anybody working
by keyboard with no way to do it at all.

### One click for a set of links

The control the other four were for. A filtered report is a set somebody has
already decided on, and ticking two hundred boxes to say so again is not less
work than clicking two hundred buttons.

So there are two controls, and the difference between them is the point:

- **Queue selected** takes the rows that were ticked. The URLs travel in the
  request body, because a share link keeps its decryption key in the URL
  fragment and a fragment is the one part of a URL a browser never sends in a
  link.
- **Queue every fetchable match** takes the *filter*. The report's query travels
  in the form's action, the server re-runs it against what the crawl recorded,
  and the URLs — keys and all — never leave the process.

That the second is also the safer half is not a coincidence: sending a set by
*describing* it beats *enumerating* it whenever the elements carry credentials.

A batch is partial rather than atomic. Two hundred links where three are
malformed and the queue has room for a hundred and fifty is a job mostly done,
not an error, and the three outcomes — queued, rejected, no room — get three
different sentences.

### The part that needed care

The queue holds a share link's decryption key longer than anything did before:
until the transfer runs, and after that until the run is evicted, because a
retry needs it again. It lives in one private dictionary, and nothing the queue
produces carries it — no snapshot, no page, no event frame, no redirect.
`tests/test_api_secret_confinement.py` reads that rather than trusting it.

The exposure is smaller than the longer life suggests. Discovery already writes
the same URL, fragment included, into SQLite, and the report renders it into a
table — a share link *is* its key, and one without it leads nowhere.

### Still not done, on purpose

- **Resume**, of a file rather than of a queue. HTTP range requests plus a byte
  offset in the metadata record; the staging directory already keeps a partial
  file out of the library.
- **A queue that survives a restart**, which cannot be built honestly before
  the above exists.
- **Parallel downloads.** A second thread on the same drain loop, waiting on a
  reason to raise the number that is about a host rather than about us.
- **Filtering the crawl list itself.** The tables inside one report have it; the
  list of crawls is still everything in the order it was recorded.
- **Selecting across pages without a filter.** "Every match" covers every page,
  which is the better control — but a hand-picked set spanning two pages still
  needs two submissions.

## Direct downloads: the file at the URL

Until this, MaxiCrawler could classify an image, count it, sort it, filter it
and show it in a report — and not fetch it. Whether a link can be downloaded is
answered by *"does a provider claim it?"*, and there was one provider: Mega.
Every ordinary file on the web fell through.

There is now a provider for the rest of the web, and it needs nothing
configured:

```bash
maxicrawler download https://example.org/reports/2026.pdf
```

In the browser, every discovered link has a **Download** button, **Queue
selected** takes the ticked ones, and **Queue every fetchable match** takes the
whole filter. So the workflow the report was built for finally ends somewhere:

1. crawl a page,
2. narrow the link table to **Type: images**,
3. press *Queue every fetchable match*,
4. watch them arrive under **Library**.

`maxicrawler info` describes them too, which it could not before:

```bash
maxicrawler info https://example.org/reports/2026.pdf
```

That still downloads nothing, and it is worth being exact about why. For every
host with an API behind it — Mega today — `info` is composed without a stream
transport, so those providers have no way to move content at all. For an
ordinary URL that would be the wrong trade: refusing the file transport would
not make the command safer, it would only make it useless on most of the web.
What keeps `info` from downloading there is that it asks `inspect` and never
`download`, and an inspection is one `HEAD`.

### "Can this be downloaded?" stopped being a useful question

It really can fetch any HTTP(S) URL, a page included, so the answer is now yes
for every link a crawl records. That is the honest answer rather than a bug,
and it has one visible consequence: the report's **Download** filter is gone,
because a control with one full bucket and one empty one is not a filter. A
bookmarked `?dl=no` still works and still says nothing matches.

What tells an image from a page is the **Type** column, which reads the URL's
own suffix and has been there since the crawl report learned to filter.

### What a file is called

`Content-Disposition` first — a host that states a name has said what it wants
the file called. Otherwise the last path segment of the URL that *answered*,
after redirects, with percent-encoding undone: `na%C3%AFve.pdf` is a name
written for a URL, not a name.

Nothing about that is trusted. Every name the library stores goes through one
sanitizer, so a host answering with `filename="../../etc/passwd"` gets a file
called `passwd` inside that download's own entry directory, and nothing else
happens. The provider reports the header faithfully and the library cleans it;
two sanitizers on one string would be one too many.

### The same guard the crawler has

This is the first provider that fetches what a *crawl* found rather than what a
host's API returned, so the private-network rule applies to it as well —
refusing loopback, private ranges, link-local space and cloud metadata
services, on the first URL and on **every redirect hop**. A refusal reads the
way the crawler's does:

```text
refused the address: 127.0.0.1 is not a public address
```

The rule itself lives in one place and is used twice: the crawler turns its
sentences into recorded skips, the download layer into failed transfers.

Two things this deliberately does **not** do. There is no size ceiling — a
transfer goes straight to disk and is expected to be large, and what bounds a
run is the queue's own limit. And robots.txt still does not apply to downloads,
as it never has: a download is an explicit act on a resource somebody named.
That mattered little when the only reachable host was Mega. It matters now,
because one filter and one click can take a site's whole image directory.

### Turning it off

`direct_downloads = false` in `maxicrawler.toml` leaves every other provider
working and stops this one advertising anything. A report then offers no
download beside an ordinary link, and the **Download** filter comes back,
because with two groups it is a filter again.

It is not a safety setting — the private-network rule applies either way. It
answers a different question: whether this installation fetches arbitrary files
at all, which is a thing an installation is entitled to decide in one place.

## The report as a workspace

A crawl finds three thousand links and you want forty of them. Everything
needed for that existed after 0.14 — searching, filtering, sorting, a queue, a
button that takes a whole filter — and doing it still meant being sent to the
queue and finding your way back to the report, twice, to queue two sets. This
milestone is about the space between the controls rather than the controls.

For a set a filter can describe it is now the filter and one button, and you
are still on the report afterwards: same filter, same sort, same page of it,
with the rows you queued saying *in queue*. For a set only a person can
describe, the checkbox in the table header ticks the page and the same button
beside it queues what is ticked.

### What is already known about each link

The report used to describe links and nothing else. It says now whether a link
is *in library* or *in queue*, as a badge on the row and as a filter beside the
others.

Both answers are set questions, and both are asked of somebody who already
knows: `LibraryService.stored` and `TransferQueue.pending`. Neither knows there
is a report; `DiscoveryService` is handed a mapping from a state to a callable
and never learns that one of them is a library and the other a queue. Adding a
state later is a member, a resolver and a label.

**They are states, not adjectives.** "in library" rather than "already
downloaded", and that is not a wording preference. Mega gives every child of a
folder the folder's own URL as its source, so one stored file inside a folder
makes the whole folder link *in library* — which is true, and which "already
downloaded" would not be. It also leaves room: a link that turns out to be a
duplicate of something stored under another address is a further state rather
than an argument about what the first one meant.

Three rules keep the marks honest, and each is a test:

- a state nothing can answer is **absent** rather than empty — an installation
  without a library does not claim every link is missing from it;
- filtering on a state nobody can answer filters **nothing** rather than
  everything, so a bookmark that predates a resolver shows the crawl instead of
  an empty table;
- a row with no state says **new** rather than nothing, because a blank cell in
  that table already means "the crawl recorded no value".

Answering them at all needed the library to stop being read one JSON document
at a time. There is a SQLite index over it now — a cache, never the authority
(ADR-037).

### The way back from a batch

Queueing forty links used to answer with the queue. That is the right answer to
"what did I just start?" and the wrong one to what somebody is actually doing,
which is working through a filtered report. You come back to the report now,
with the filter, the sort, the page and the columns exactly as they were, and a
one-line confirmation of what the batch did.

The two buttons get there differently, and the difference is the decision
(ADR-039). **Queue every fetchable match** already carries the filter in its
action, so the server rebuilds the way back from it: two copies of one filter
are two things that can disagree, and the copy a browser holds is the one that
could be made to point somewhere else. **Queue selected** posts a set of ticked
URLs and no query at all, so it is told where to go back to in a form field —
and everything a browser sends goes through a check that accepts a path of ours
and nothing else, `//elsewhere.test/` included, which is how a "go back
afterwards" parameter turns into an open redirect.

The ticks themselves do not come back. Carrying them would mean putting the
URLs in a query string, which is the one place a share link's key must never go
(ADR-020). What comes back instead is the rows saying *in queue* — the same
information without the credential, and true of the ones somebody else queued
too.

The confirmation lasts exactly one page. It lives in the redirect rather than
in a session, and the parameters that carry it are dropped by the same function
that carries every other parameter forward, so nothing has to remember that it
has already shown one.

### Two hundred boxes, one click

The header of the link table has a checkbox that ticks every row on the page,
and a counter beside the button that says how many are ticked. Both are
rendered hidden and revealed by the script that gives them meaning — a checkbox
that ticks nothing is worse than no checkbox.

"Every link on this page" is meant exactly. The other button queues every link
the *filter* matches, which is a different set, is resolved on the server, and
is not bounded by two hundred.

### A report you can fit on the screen you have

The summary, the page table and the link table fold from their own headings,
and the fold is carried by every link on the page — which is the difference
between staying folded and folding once. A folded panel keeps its heading and
its count, so this is a way of arranging the report rather than of losing it.

A link and a query parameter rather than a `<details>`, which the three
breakdowns inside the summary still are. That difference is the point: a
`<details>` forgets on every click, which is right for something you open to
read once and wrong for a table you are keeping out of your way for an
afternoon.

What a link was *written as* before normalization is a column now rather than a
second line under the URL, so a crawl of a site that rewrites its URLs no
longer doubles the height of every affected row — and it turns off in one click
like every other column.

### The queue, wherever you are

Every page but the queue's own carries a line in the top bar saying what the
queue is doing: how many downloading, how many waiting, how many failed, and
whether it is paused. Counts only — the line naming the file being fetched
already exists on the two pages with room for it.

It is read once per page render rather than streamed, from a call that
deliberately does not build a snapshot of five hundred waiting requests. A
count a few seconds old still answers the question the line is for.

Paused is said even when the queue is empty. It is the answer to "why is
nothing happening", which is asked of an empty queue as often as of a full one.

### Two hundred files, one page

The queue page followed a running transfer by reloading itself when that
transfer ended. On two hundred files that is two hundred page loads and two
hundred lost scroll positions.

It asks for the panels instead. Everything a finished transfer changes lives in
one partial that the page includes and that `/downloads?part=queue` answers on
its own — the same template, the same snapshot, the same view function, so what
a reload produces and what a swap produces cannot drift apart.

The script decides nothing (ADR-038). It reads three attributes the server
writes into an empty element: which stream to listen to, where to ask when that
stream ends, and where the answer goes. One download's page renders the first
and not the other two, which is the whole of how one script serves two pages
that mean different things by "finished" — there the server has more to say
than a swap could carry, and asking for the page is right once.

A missing stream is a signal rather than a silence. There are three states, not
two: something to listen to, nothing left to do, and the moment *between* two
transfers where the queue is busy and the worker has not picked the next one
up. A page that read the third as the second would stop following a batch at
whichever file lost that race, and a rare race rolled two hundred times is not
rare.

### How far along the whole queue is

Above the counters is a bar across the queue — "41 of 200 finished" — and
beside it a rate. The denominator grows when more is queued, which is honest
rather than awkward: there is no batch here, only a queue.

**No estimate of how much longer, deliberately.** A waiting request has not
been inspected, so nothing knows what it points at, whether it is one file or
two hundred, or how large any of them is. "About twelve minutes left" would be
invented rather than measured, and an interface that guesses once is one nobody
believes the second time. The rate is the opposite: bytes over the time
actually spent transferring, not over the wall clock — a queue that sat paused
overnight did not get slower while it was paused — and it says "while
transferring" so it cannot be read as what the line is doing this second.

The counters survive the rows they were counted from. The history keeps fifty
entries and every number above it was a sum over the entries still held, so the
next thing queued after an afternoon of downloads dropped everything past the
fiftieth and the counters went with it — the second batch opened on a page that
had forgotten the first. What eviction drops is now folded into a total first:
the row goes, the number stays. Clearing the list is the only thing that resets
them, which is what makes them worth reading the rest of the time.

The history offers **Try all N again** and **Clear the list**. The first takes
the same set the rows offer one at a time — everything that ended without the
file arriving, a request somebody stopped included — and says how many, so
nobody finds out afterwards what "everything" turned out to mean; it appears
only above one row, since a button doing what the single button beside it does
teaches nothing. The second empties the list and the counters over it together,
because the counters are totals over exactly those rows, and it is the only
thing that resets them. The files are in the library either way, which is what
the footnote under the table has said since the table existed.

### The part that needed care

**A tally is not a snapshot.** The line in the top bar needed four numbers and
was rendering on every page of the interface; a snapshot builds one object per
waiting request, and a full queue is five hundred of them. So there is a second,
cheaper reading — and because two readings of one queue is one bug waiting for a
slow afternoon, the failures in it are counted through the same snapshots the
tables read, and a test holds the two together.

**A context key the chrome shares with a page is a key one of them silently
loses.** The queue line is merged into every page's context by the same function
that merges the page's own; the queue page passes its context under `queue`, so
the line arrived on `/downloads` with every field empty. It is called
`queue_strip` now. What made it survive a test run is worth recording too: a
`-k` expression that looked like it selected the test that would have caught it
and selected six others instead.

**Nothing in a test suite lays a page out.** The two commits that changed how
the report and the queue look were checked in a browser instead: the fold link
sits flush at the panel's right padding in all three headings; folding the
summary and the page table moves the link table from 961 pixels down the page to
276; and a queue of two hundred drained through thirty-six panel swaps in one
document with the scroll position identical at every one of them.

### Still not done, on purpose

**A stable identity for a library entry.** The column is reserved and stays
empty, and that is now a decision rather than a gap (ADR-037). An entry already
has a stable identity in its directory key; what a separate one would add is
independence from the *address*, for a file reached through two links. A random
id would be the one thing in the library that cannot be recomputed from the file
system, which is the property ADR-010 exists to keep — restore from a backup and
every reference to it dangles. A derived one would be a second name for the key.
The natural anchor for duplicates is the checksum, which is both
address-independent and recomputable, so the column waits for the question that
will choose it.

**The queue still does not decline what it already holds.** The report tells you
before you click, which is most of the value; the queue accepting the same URL
twice is a decision about what a refusal means and has not been made.

**The list of crawls is still everything, in the order it was recorded.** The
tables *inside* one report have had searching, filtering, sorting and paging
since 0.14. This is the point at which htmx would earn being vendored, and there
is a measurement behind that now: the fragment swap this milestone needed cost
about forty lines and no dependency (ADR-038). The question is how many more
places want their own forty.

## The library as a workspace

A crawl of an image directory leaves nine hundred files, and the question after
that is not *what is in here* — the listing has answered that since 0.12 — but
*which of these do I want to keep*. Answering it used to mean a page per file
and a way back per file: eighteen hundred page loads and eighteen hundred lost
scroll positions.

The library shows tiles now, every file carries four judgements wherever it
appears, and a file opened from a listing knows it is the twelfth of forty.

### Tiles, or rows

`?view=grid` and `?view=list`, grid by default, and the state lives in the URL —
a bookmarked filtered grid opens as a filtered grid. Both render from the same
listing; what differs is the template and how many fit on a page (sixty against
fifty).

A tile shows the file, its name shortened in the middle so the extension
survives, its size, and its judgement. **Nothing is generated inside the
request.** For an image it shows a thumbnail where one has been made, the stored
image below `preview_inline_bytes` (1 MB by default) where none has, and a
symbol above that. Text and Markdown show their first lines, read from the file;
everything else shows a symbol.

### Thumbnails

Make them with a run of their own, after installing the extra:

```bash
uv run python scripts/make_thumbnails.py --config settings.toml --apply
```

About forty photographs a second, so a few thousand images is a couple of
minutes once and seconds every time after — what exists is skipped. Nothing
makes one on demand: a page of sixty tiles would be sixty image decodes inside
one request.

**Why a byte limit was not enough.** A file's size says what is sent; a browser
holds the decoded image at four bytes a pixel however little that was. Measured
on a real library of 22,692 entries: 2% of its images are under a megapixel,
27% of the ones the limit still lets through are over four, and the sixty
largest of those are **3.3 GB of bitmap on a single page**. Meanwhile 47% sat
above the limit and had no preview at all. A thumbnail therefore wins whenever
there is one, not only above some size.

**A thumbnail is only ever a cache** (ADR-044). It can be deleted in full at any
moment, it lives beside the database and never inside `library/`, and it is
never something an entry says about itself. It is addressed by the checksum the
record already carries, so two entries holding the same picture share one. The
same run sweeps up the ones no entry can reach any more.

Without the extra installed, every tile behaves exactly as it did before
thumbnails existed.

### Four verbs

| Button | What it records | The file | Offered again? |
| --- | --- | --- | --- |
| **Keep** | `kept` | stays | it is already here |
| **Ignore** | `ignored` | stays | **no** |
| **Discard** | `discarded` | **deleted** | **no** |
| **★** | a switch, independent of the verdict | stays | — |

Ignoring and discarding are two decisions, not one with a stronger adverb: *this
does not interest me but it is not in the way*, against *take the bytes back*.
Every judgement can be taken back, including a discard — which does not bring the
file back, but lifts the block, so downloading the link again restores it.

They are on the tile, on the row, and on the file's own page, and a selection of
them can be judged at once through the same checkbox mechanism the report uses.
Discarding a batch asks first, on a page naming every file and how much space it
frees.

### A judgement survives the next download

It is written into that file's own metadata document, not into the database. The
index is a cache that may be deleted and rebuilt, and a judgement a rebuild loses
is not a judgement — this way a library moved to another machine arrives with
everything anybody decided about it.

A re-download used to rebuild the record from the job, which is what made this a
question at all: it would have silently dropped the review. It now carries the
review across untouched, along with the unknown members a document is promised to
keep (ADR-013). Two writers, disjoint fields (ADR-040): a download rebuilds the
transfer fields and touches nothing else, judging rebuilds the review and touches
nothing else.

### What was thrown away is not fetched again

The record stays behind as a headstone — with the file's name, size and checksum
— and it is the only thing that stops the next *"queue every match"* from
downloading the file again, because *"the library holds this"* is answered by the
record **and** the file.

The promise is kept in three places, because two would make it a lie: the
download worker turns such a request away, a report marks the link *dismissed*
beside *in library* and *in queue*, and *"queue every fetchable match"* leaves it
out — so it never enters the queue only to be refused at the far end, where a
refusal reads as a fault.

A link counts as dismissed only when **everything** recorded under it is. A Mega
folder gives every file inside it the folder's own URL, and one dismissed
thumbnail must not put a folder of two hundred out of reach.

### Filters for the question being asked

The chips above the listing narrow it in one click and carry their counts:
review, source, type, state, and size. Type is new — image, video, audio, PDF,
document, archive, text, other — and is a classification of its own beside the
table that decides what a browser may be shown. The two are deliberately
separate: the second is a security boundary and an allow-list, so a `.rar` gets
a category and still never gets a content type.

The size chips are the four usual bands and are the one row without counts: a
band's count would have to be computed over the whole library, and the number
that matters — how many the chosen band holds — is already at the top of the
page. Beside them are `Larger than` and `Smaller than`, which read what they
print, so `10 MB` typed into a box and 10000000 carried by a chip are one
filter.

Every chip is a toggle: standing on one, it links back to the listing without it.
A group holding a single chip is not drawn at all, because its two states would
show the same rows.

### One file, and somewhere to work through a listing of them

A file's page is two columns: the file as large as the space allows, and a narrow
column of what is known about it with the buttons underneath. Audio and video
join the things a browser is handed directly, with their own ceiling —
`max_view_bytes` exists because a browser chokes on a 400 MB text file, which is
not the situation a `<video>` requesting ranges is in.

Opened **from a listing**, the page says `12 of 340`, links either way, and a
verdict moves on to the next file in that listing. Opened on its own it is the
page it always was. The successor is worked out *before* the verdict is written:
under the *unreviewed* filter the file being judged leaves the set as the verdict
lands, so looking afterwards would skip one file on every click. Taking a
judgement back and starring stay where they are — a correction belongs on the
thing it corrects.

With scripting on, `k`, `i`, `x` and `f` are the four verbs, the arrow keys are
the two neighbours, and `Enter` opens the file on its own. Every one of them
presses a control that is on the page anyway; nothing is reachable by keyboard
only. **`x` deletes a file with one keystroke and does not ask** — it is the one
key here worth knowing about before using the others.

### Too small to be worth having

An image directory answers with a thumbnail, a sprite and an icon for every
picture in it. `min_download_size` (100 000 bytes by default, `0` turns it off)
refuses those, and it does it in the sink where every provider's bytes pass
anyway: once when a size is announced, so nothing is transferred, and once when
the last byte lands, for a server that announced none. In the second case the
file is still staged outside the library and is discarded without ever having
been visible.

It applies to a single download somebody clicked as well. One rule in one place;
two rules would need two explanations. Every refusal is visible with both sizes
in the queue's history, and writes a record — otherwise the decision is gone
after a restart and the next bulk queue fetches the same file again.

### A form of ours, or none at all

The interface has no authentication and says so (ADR-025). That was bounded while
the worst a stray request could do was start a crawl; it stopped being bounded
when a button began deleting files, because a page in any other tab can submit a
form at this server and the browser will send it.

Every unsafe method now has to have come from a page of ours, decided from
`Sec-Fetch-Site` — set by the browser, unreachable from script — and from
`Origin` where that header is missing. A request with neither is allowed, which
is a decision: what sends neither is `curl`, a script, a test, something already
on the machine. No token, no session, no cookie, so every form still works
without JavaScript. It is not authentication and does not become any (ADR-043).

### Configuration

```toml
[maxicrawler]
min_download_size = 100000     # 0 turns the floor off
preview_inline_bytes = 1000000 # above this a tile is a symbol
max_view_bytes = 33554432      # inline documents
max_stream_bytes = 0           # audio and video; 0 is no limit
```

### The part that needed care

**A tombstone is a promise about three places.** Refusing a discarded record in
the worker is the obvious one and the least useful on its own: what somebody
actually presses is *"queue every match"*, and a link that goes into the queue to
be turned away at the far end looks like a failure rather than like a decision.
The worker also writes nothing when it refuses — a record rebuilt to say
"refused" would lose the status and the file details the entry already had.

**Auto-advance was verified by watching a number shrink.** Pressing *Keep* on
`1 of 6` of an unreviewed listing lands on the next file, whose page reads
`1 of 5`. That is the proof the successor was chosen before the write: the judged
row had left the set.

**Two things were checked in a browser rather than asserted.** A range request
against a stored video is answered with `206` and a `content-range`, which is
what makes seeking possible; and a cross-site POST at the discard route is
answered with `403` while the same form from the interface goes through.

### Still not done, on purpose

**No video or PDF thumbnails.** Images have them now — see below — and the other
two would need ffmpeg and a PDF renderer, each of which is its own decision.

**No comments and no tags**, not even as a reserved member. An empty field in a
document is a promise.

**No duplicate detection.** The checksum is recorded and the chips take another
group without changing how a query is written, which is the whole of the
preparation — the question about identity that would decide it is the one 0.15
deliberately left open.

**Nothing locks an entry.** A download finishing at the same moment as a
judgement can lose one of the two writes. What bounds it is that the two writers
touch different members, so the worst case is one judgement lost rather than a
document describing a file that is not there.

## Documentation

| Document | Purpose |
| --- | --- |
| [VISION.md](VISION.md) | Mission, core principles, and explicit non-goals. |
| [ROADMAP.md](ROADMAP.md) | Milestones from 0.1 to the stable release. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Layer boundaries and quality rules. |
| [DECISIONS.md](DECISIONS.md) | Architecture decision records. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Workflow and coding guidelines. |
| [docs/architecture.md](docs/architecture.md) | Design rules and the plugin architecture in detail. |
| [docs/development.md](docs/development.md) | Contributor setup and quality gates. |

## Development

Run the hooks before contributing:

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

Create focused changes, add tests for behavior, and ensure all commands in the
quick start section pass. The CI workflow runs the same quality gates on pull
requests.

## Responsible crawling

MaxiCrawler obeys robots.txt by default, honours a host's `Crawl-delay`, and
refuses to reach into private address space unless told to. None of that makes
you unaccountable for what you point it at: users remain responsible for
complying with websites' terms, applicable law, and sensible rate limits — and
the switches that turn each of these off exist for the cases where somebody has
decided, not so that nobody has to.

## License

MaxiCrawler is released under the [MIT License](LICENSE).
