"""TOML-backed configuration models."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("maxicrawler.toml")


@dataclass(frozen=True, slots=True)
class Settings:
    """Application settings loaded from a TOML document."""

    user_agent: str = "MaxiCrawler/0.1.0"
    database_path: Path = Path("maxicrawler.db")
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if not self.user_agent.strip():
            msg = "user_agent must not be empty"
            raise ValueError(msg)
        if not self.log_level.strip():
            msg = "log_level must not be empty"
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
            log_level=_string_value(app_config, "log_level", defaults.log_level).upper(),
        )

    def to_toml(self) -> str:
        """Serialize the settings as a human-editable TOML document."""
        return (
            "[maxicrawler]\n"
            f'user_agent = "{self.user_agent}"\n'
            f'database_path = "{self.database_path.as_posix()}"\n'
            f'log_level = "{self.log_level}"\n'
        )


def _string_value(values: dict[str, Any], key: str, default: str) -> str:
    value = values.get(key, default)
    if not isinstance(value, str):
        msg = f"{key} must be a string"
        raise ValueError(msg)
    return value
