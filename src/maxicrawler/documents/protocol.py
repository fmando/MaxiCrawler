"""The common interface every document reader implements."""

from pathlib import Path
from typing import Protocol, runtime_checkable

from maxicrawler.documents.models import Document, DocumentType


@runtime_checkable
class DocumentReader(Protocol):
    """Reads one family of file formats into a :class:`Document`.

    The interface is deliberately format-agnostic: callers select a reader by
    file suffix and then work with :class:`Document` only. Readers perform
    file-system I/O but never any network access.
    """

    @property
    def document_type(self) -> DocumentType:
        """Return the format this reader produces."""
        ...

    @property
    def suffixes(self) -> frozenset[str]:
        """Return the lower-case file suffixes this reader claims, dot included."""
        ...

    def read(self, path: Path) -> Document:
        """Read *path* and return its document representation."""
        ...
