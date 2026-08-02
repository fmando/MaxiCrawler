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


def _float_value(values: dict[str, Any], key: str, default: float) -> float:
    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"{key} must be a number"
        raise ValueError(msg)
    return float(value)
