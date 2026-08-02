"""Extraction protocols."""

from typing import Protocol


class Extractor(Protocol):
    """Transforms a document into a structured mapping."""

    def extract(self, document: str) -> dict[str, str]: ...
