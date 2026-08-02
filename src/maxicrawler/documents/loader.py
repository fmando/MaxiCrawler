"""Selection of document readers and traversal of local sources."""

from collections.abc import Iterable, Iterator
from pathlib import Path

from maxicrawler.documents.models import Document
from maxicrawler.documents.protocol import DocumentReader
from maxicrawler.documents.readers import (
    HtmlDocumentReader,
    MarkdownDocumentReader,
    TextDocumentReader,
)


class DocumentError(RuntimeError):
    """Base class for document loading failures."""


class UnsupportedDocumentError(DocumentError):
    """Raised when no registered reader claims a file suffix."""


def default_readers() -> tuple[DocumentReader, ...]:
    """Return the readers for the built-in supported formats."""
    return (TextDocumentReader(), MarkdownDocumentReader(), HtmlDocumentReader())


class DocumentLoader:
    """Maps files to readers and walks local files and directories.

    The loader composes readers rather than subclassing them, so supporting a
    new format means passing an extra :class:`DocumentReader`. Traversal is
    sorted, making discovery runs reproducible, and skips hidden directories
    such as ``.git``.
    """

    def __init__(self, readers: Iterable[DocumentReader] | None = None) -> None:
        self._readers: tuple[DocumentReader, ...] = (
            tuple(readers) if readers is not None else default_readers()
        )
        self._by_suffix: dict[str, DocumentReader] = {
            suffix: reader for reader in self._readers for suffix in reader.suffixes
        }

    @property
    def readers(self) -> tuple[DocumentReader, ...]:
        """Return the readers this loader composes."""
        return self._readers

    @property
    def supported_suffixes(self) -> frozenset[str]:
        """Return every file suffix the loader can read."""
        return frozenset(self._by_suffix)

    def reader_for(self, path: Path) -> DocumentReader | None:
        """Return the reader claiming *path*, or ``None`` if unsupported."""
        return self._by_suffix.get(path.suffix.lower())

    def supports(self, path: Path) -> bool:
        """Return whether any reader claims *path*."""
        return self.reader_for(path) is not None

    def read(self, path: Path) -> Document:
        """Read a single file.

        Raises:
            UnsupportedDocumentError: no reader claims the file suffix.
        """
        reader = self.reader_for(path)
        if reader is None:
            msg = f"unsupported document type: {path}"
            raise UnsupportedDocumentError(msg)
        return reader.read(path)

    def iter_paths(self, root: Path) -> Iterator[Path]:
        """Yield every supported file at or below *root*, in sorted order.

        A file *root* yields itself when supported; a directory is walked
        recursively. Unsupported files are skipped silently.

        Raises:
            FileNotFoundError: *root* does not exist.
        """
        if not root.exists():
            msg = f"path does not exist: {root}"
            raise FileNotFoundError(msg)
        if root.is_file():
            if self.supports(root):
                yield root
            return
        for path in sorted(root.rglob("*")):
            if path.is_file() and self.supports(path) and not self._is_hidden(path, root):
                yield path

    def load_all(self, root: Path) -> Iterator[Document]:
        """Read every supported document at or below *root*."""
        for path in self.iter_paths(root):
            yield self.read(path)

    @staticmethod
    def _is_hidden(path: Path, root: Path) -> bool:
        """Return whether *path* sits inside a hidden directory below *root*."""
        relative = path.relative_to(root)
        return any(part.startswith(".") for part in relative.parts[:-1])
