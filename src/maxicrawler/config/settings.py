"""Configuration models."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    """Core crawl policy settings with conservative defaults."""

    user_agent: str = "MaxiCrawler/0.1.0"
    request_delay_seconds: float = 1.0
    max_pages: int = 100

    def __post_init__(self) -> None:
        if self.request_delay_seconds < 0:
            msg = "request_delay_seconds must be non-negative"
            raise ValueError(msg)
        if self.max_pages < 1:
            msg = "max_pages must be at least one"
            raise ValueError(msg)
