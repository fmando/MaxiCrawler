"""Format-specific document readers."""

from html.parser import HTMLParser
from pathlib import Path

from maxicrawler.documents.models import Document, DocumentType

LINK_ATTRIBUTES = frozenset({"href", "src", "data", "cite", "action", "poster", "longdesc"})
"""HTML attributes whose value is a link target."""

NON_CONTENT_TAGS = frozenset({"script", "style"})
"""HTML elements whose text content is code, not prose."""


def read_source_text(path: Path) -> str:
    """Return the decoded content of *path*.

    Undecodable bytes are replaced rather than raised, so a single damaged
    file cannot abort a whole discovery run.
    """
    return path.read_text(encoding="utf-8", errors="replace")


class TextDocumentReader:
    """Reads plain text files."""

    @property
    def document_type(self) -> DocumentType:
        """Return :attr:`DocumentType.TEXT`."""
        return DocumentType.TEXT

    @property
    def suffixes(self) -> frozenset[str]:
        """Return the suffixes claimed by this reader."""
        return frozenset({".txt"})

    def read(self, path: Path) -> Document:
        """Read *path* verbatim; plain text carries no markup links."""
        return Document(
            path=path,
            document_type=self.document_type,
            text=read_source_text(path),
        )


class MarkdownDocumentReader:
    """Reads Markdown files.

    Markdown keeps every link target literally in the source — inline
    ``[text](url)``, autolinks ``<url>``, and reference definitions alike — so
    the raw content is already the complete URL-bearing text.
    """

    @property
    def document_type(self) -> DocumentType:
        """Return :attr:`DocumentType.MARKDOWN`."""
        return DocumentType.MARKDOWN

    @property
    def suffixes(self) -> frozenset[str]:
        """Return the suffixes claimed by this reader."""
        return frozenset({".md"})

    def read(self, path: Path) -> Document:
        """Read *path* verbatim."""
        return Document(
            path=path,
            document_type=self.document_type,
            text=read_source_text(path),
        )


class _LinkCollectingParser(HTMLParser):
    """Collects link attributes and prose while tolerating malformed markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.text_parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in NON_CONTENT_TAGS:
            self._skip_depth += 1
        for name, value in attrs:
            if name.lower() in LINK_ATTRIBUTES and value:
                self.links.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in NON_CONTENT_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.text_parts.append(data)


class HtmlDocumentReader:
    """Reads HTML files, separating prose from markup link targets.

    Parsing uses the standard library's :class:`~html.parser.HTMLParser`, so no
    third-party dependency is introduced and malformed markup is tolerated.
    Character references in both text and attribute values are resolved.
    """

    @property
    def document_type(self) -> DocumentType:
        """Return :attr:`DocumentType.HTML`."""
        return DocumentType.HTML

    @property
    def suffixes(self) -> frozenset[str]:
        """Return the suffixes claimed by this reader."""
        return frozenset({".html", ".htm"})

    def read(self, path: Path) -> Document:
        """Read *path*, returning its prose and its markup link targets."""
        parser = _LinkCollectingParser()
        parser.feed(read_source_text(path))
        parser.close()
        return Document(
            path=path,
            document_type=self.document_type,
            text="".join(parser.text_parts),
            links=tuple(parser.links),
        )
