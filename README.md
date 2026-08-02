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

Run the test suite and checks:

```bash
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

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
