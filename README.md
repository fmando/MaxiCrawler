# MaxiCrawler

**MaxiCrawler** is a modular Python 3.12+ foundation for building responsible,
extensible web crawlers. It keeps crawling, extraction, persistence, plugins,
and delivery interfaces separate so each concern can evolve independently.

> Version: **0.1.0** — the project is in its initial, pre-alpha phase.

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
| `crawler` | Coordinates URL fetching and crawl lifecycle. |
| `extractors` | Converts responses into structured content. |
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

See [docs/architecture.md](docs/architecture.md) for design rules and
[docs/development.md](docs/development.md) for the contributor workflow.

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
