"""Immutable value objects describing a read document.

These models live outside :mod:`maxicrawler.domain` on purpose: they carry a
filesystem :class:`~pathlib.Path`, which is an infrastructure concern the
domain layer must stay free of.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class DocumentType(StrEnum):
    """The supported document formats."""

    TEXT = "text"
    MARKDOWN = "markdown"
    HTML = "html"


@dataclass(frozen=True, slots=True)
class Document:
    """A document that has been read into memory.

    ``text`` holds the human-readable content. ``links`` holds link targets
    that only exist in markup and would be invisible in ``text``, such as the
    ``href`` of an HTML anchor. Keeping the two apart lets a single, generic
    URL extractor serve every format.
    """

    path: Path
    document_type: DocumentType
    text: str
    links: tuple[str, ...] = ()

    @property
    def source(self) -> str:
        """Return a stable, platform-independent identifier for the document."""
        return self.path.as_posix()
