"""Plugin extension protocols."""

from typing import Protocol


class Plugin(Protocol):
    """A named MaxiCrawler extension."""

    name: str

    def register(self) -> None: ...
