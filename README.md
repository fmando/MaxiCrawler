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
- Links found on a page feed the same discovery pipeline and the same plugins local documents use.
- A provider-independent download manager: a new host is a plugin and a provider, nothing else.
- A self-describing library: one directory per resource, with versioned JSON metadata beside it.
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

`--all-extras` includes three optional extras. `mega` pulls in `cryptography`
and is needed only to decrypt the names inside a Mega share. `brotli` lets the
crawler read a Brotli-compressed page; without it, `Accept-Encoding` simply
does not advertise `br`, so a server sends gzip instead. `web` is the browser
interface. Everything else works without any of them.

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
uv run mypy src
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

Without a `stream=` transport the registry produces inspection-only providers,
which is what keeps `info` unable to download by construction rather than by
convention.

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
- **No robots.txt — yet.** Fetching one page named by its operator is what a
  browser does when the same person types the same address. The architecture
  reserves a one-method `CrawlPolicy` seam for it, and a `RobotsPolicy` will
  read `/robots.txt` through the same fetcher. Until then, **you** are
  responsible for what you point it at.

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
| `--max-pages` | `50` | stop after this many pages |
| `--json` | off | the machine-readable report |
| `--prose` / `--no-prose` | prose | also read URLs written as plain text |
| `--persist` / `--no-persist` | persist | store the summary and the URLs |

Every default except the last two is configurable — `crawl_depth`,
`crawl_max_pages` and `crawl_same_domain` in `maxicrawler.toml`.

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
| 5 | the starting page could not be retrieved |
| 6 | the starting page was not a page |
| 7 | the crawl was interrupted |

### Still not done, on purpose

- **robots.txt.** Unchanged from Sprint 8, and the stakes are higher now that a
  crawl fetches many pages rather than one. The `CrawlPolicy` seam is there and
  a `RobotsPolicy` will read `/robots.txt` through the same fetcher. Until then,
  **what you point this at is your responsibility.**
- **No delay between requests.** Politeness, rate limits and scheduling belong
  with robots.txt and are one subject; the `ThrottledFetcher` seam is documented
  and empty.
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
| **Library** | Named, and empty. Listing it will go through a service, the way crawling does. (Sprint 11 filled it in.) |
| **Settings** | The configuration as it was read, and which file it came from. Read-only. |

Naming all four from the beginning is deliberate. Two of them do very little
yet, and saying so is cheaper than rearranging every page around them later.

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

Users are responsible for complying with websites' terms, robots directives,
applicable law, and sensible rate limits. MaxiCrawler is deliberately designed
to make transport policies and crawl behavior explicit rather than bypassing
access controls.

## License

MaxiCrawler is released under the [MIT License](LICENSE).
