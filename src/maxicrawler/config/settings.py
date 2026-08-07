"""TOML-backed configuration models."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("maxicrawler.toml")


@dataclass(frozen=True, slots=True)
class Settings:
    """Application settings loaded from a TOML document.

    The network fields are used only by the commands that talk to a provider.
    Discovery never reads them, because discovery never leaves the machine.
    """

    user_agent: str = "MaxiCrawler/0.1.0"
    database_path: Path = Path("maxicrawler.db")
    library_path: Path = Path("library")
    """Where downloads are stored; ``--output`` overrides it for one run."""

    log_level: str = "INFO"
    network_timeout: float = 30.0
    network_retries: int = 3
    max_entries: int = 1000

    max_page_bytes: int = 8 * 1024 * 1024
    """Upper bound on a crawled page, before and after decompression."""

    max_redirects: int = 5
    """How many hops one fetch may follow before the chain is called a loop."""

    max_links: int = 10_000
    """How many links one page may contribute before the rest are dropped."""

    crawl_depth: int = 0
    """Default link distance a crawl follows; zero fetches the seed alone."""

    crawl_max_pages: int = 50
    """Default ceiling on how many pages one crawl fetches."""

    crawl_same_domain: bool = False
    """Whether a crawl stays on the seed's host unless told otherwise.

    Off, so that following a share link to Mega or Pixeldrain keeps working.
    Configurable here rather than hard-coded, because which of the two
    workflows an installation mostly serves is an installation's business.
    """

    def __post_init__(self) -> None:
        if not self.user_agent.strip():
            msg = "user_agent must not be empty"
            raise ValueError(msg)
        if not str(self.library_path).strip():
            msg = "library_path must not be empty"
            raise ValueError(msg)
        if not self.log_level.strip():
            msg = "log_level must not be empty"
            raise ValueError(msg)
        if self.network_timeout <= 0:
            msg = "network_timeout must be positive"
            raise ValueError(msg)
        if self.network_retries < 1:
            msg = "network_retries must be at least 1"
            raise ValueError(msg)
        if self.max_entries < 1:
            msg = "max_entries must be at least 1"
            raise ValueError(msg)
        if self.max_page_bytes < 1:
            msg = "max_page_bytes must be at least 1"
            raise ValueError(msg)
        if self.max_redirects < 0:
            msg = "max_redirects must not be negative"
            raise ValueError(msg)
        if self.max_links < 1:
            msg = "max_links must be at least 1"
            raise ValueError(msg)
        if self.crawl_depth < 0:
            msg = "crawl_depth must not be negative"
            raise ValueError(msg)
        if self.crawl_max_pages < 1:
            msg = "crawl_max_pages must be at least 1"
            raise ValueError(msg)

    @classmethod
    def from_toml(cls, path: Path = DEFAULT_CONFIG_PATH) -> "Settings":
        """Load settings from *path*, returning defaults when it does not exist."""
        if not path.exists():
            return cls()
        with path.open("rb") as config_file:
            raw_config: dict[str, Any] = tomllib.load(config_file)
        app_config = raw_config.get("maxicrawler", raw_config)
        if not isinstance(app_config, dict):
            msg = "[maxicrawler] must be a TOML table"
            raise ValueError(msg)
        defaults = cls()
        return cls(
            user_agent=_string_value(app_config, "user_agent", defaults.user_agent),
            database_path=Path(
                _string_value(app_config, "database_path", str(defaults.database_path))
            ),
            library_path=Path(
                _string_value(app_config, "library_path", str(defaults.library_path))
            ),
            log_level=_string_value(app_config, "log_level", defaults.log_level).upper(),
            network_timeout=_float_value(app_config, "network_timeout", defaults.network_timeout),
            network_retries=_int_value(app_config, "network_retries", defaults.network_retries),
            max_entries=_int_value(app_config, "max_entries", defaults.max_entries),
            max_page_bytes=_int_value(app_config, "max_page_bytes", defaults.max_page_bytes),
            max_redirects=_int_value(app_config, "max_redirects", defaults.max_redirects),
            max_links=_int_value(app_config, "max_links", defaults.max_links),
            crawl_depth=_int_value(app_config, "crawl_depth", defaults.crawl_depth),
            crawl_max_pages=_int_value(app_config, "crawl_max_pages", defaults.crawl_max_pages),
            crawl_same_domain=_bool_value(
                app_config, "crawl_same_domain", defaults.crawl_same_domain
            ),
        )

    def to_toml(self) -> str:
        """Serialize the settings as a human-editable TOML document."""
        return (
            "[maxicrawler]\n"
            f'user_agent = "{self.user_agent}"\n'
            f'database_path = "{self.database_path.as_posix()}"\n'
            f'library_path = "{self.library_path.as_posix()}"\n'
            f'log_level = "{self.log_level}"\n'
            f"network_timeout = {self.network_timeout}\n"
            f"network_retries = {self.network_retries}\n"
            f"max_entries = {self.max_entries}\n"
            f"max_page_bytes = {self.max_page_bytes}\n"
            f"max_redirects = {self.max_redirects}\n"
            f"max_links = {self.max_links}\n"
            f"crawl_depth = {self.crawl_depth}\n"
            f"crawl_max_pages = {self.crawl_max_pages}\n"
            f"crawl_same_domain = {str(self.crawl_same_domain).lower()}\n"
        )


def _string_value(values: dict[str, Any], key: str, default: str) -> str:
    value = values.get(key, default)
    if not isinstance(value, str):
        msg = f"{key} must be a string"
        raise ValueError(msg)
    return value


def _int_value(values: dict[str, Any], key: str, default: int) -> int:
    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{key} must be an integer"
        raise ValueError(msg)
    return value


def _bool_value(values: dict[str, Any], key: str, default: bool) -> bool:
    value = values.get(key, default)
    if not isinstance(value, bool):
        msg = f"{key} must be true or false"
        raise ValueError(msg)
    return value


def _float_value(values: dict[str, Any], key: str, default: float) -> float:
    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"{key} must be a number"
        raise ValueError(msg)
    return float(value)
