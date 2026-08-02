# MaxiCrawler

**MaxiCrawler** is a modular Python 3.12+ foundation for building responsible,
extensible web crawlers. It keeps crawling, extraction, persistence, plugins,
and delivery interfaces separate so each concern can evolve independently.

> Version: **0.1.0** — the project is in its initial, pre-alpha phase.

MaxiCrawler is a link discovery and management platform, not merely a
downloader. [VISION.md](VISION.md) states the mission, the core principles, and
what the project deliberately will not do.

## Features

- Clear package boundaries for crawling, extraction, downloads, databases, plugins, GUI, and API layers.
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

`--all-extras` includes the optional `mega` extra, which pulls in
`cryptography`. It is needed only to decrypt the names inside a Mega share;
everything else works without it.

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

Ask what a share link points at, without downloading it:

```bash
uv run maxicrawler info "https://mega.nz/file/<handle>#<key>"
```

Run the test suite and checks:

```bash
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

## Two extension layers

MaxiCrawler learns about a new host in two independent steps, and it is worth
knowing which one you are looking at:

| Layer | Package | Question | Network |
| --- | --- | --- | --- |
| Plugin | `maxicrawler.plugins` | *"Can I classify this URL?"* | never |
| Provider | `maxicrawler.providers` | *"What can I do with this resource?"* | allowed |

A plugin decides from the URL string alone, so it runs on every URL discovery
finds and can never block. A provider takes the plugin's verdict and asks the
host what the resource actually is. Only commands that say so contact a
provider: `discover` stays entirely offline, `info` is the one that reaches out.

## Architecture

The public package lives under `src/maxicrawler/`. Each subpackage owns one
responsibility and communicates through typed, small interfaces.

| Package | Responsibility |
| --- | --- |
| `crawler` | Coordinates the discovery lifecycle and orchestration services. |
| `documents` | Reads local files into a format-independent representation. |
| `extractors` | Converts documents and responses into structured content. |
| `downloader` | Fetches resources and applies transport policies. |
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

### Nothing is downloaded

For a Mega file share the provider sends `{"a":"g","p":<handle>}` and leaves the
download flag unset. Mega then reports size and encrypted attributes without
allocating a transfer URL, so no file content moves and no transfer quota is
consumed. A folder share is one `{"a":"f","c":1,"r":1}` request that returns the
whole node tree.

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
