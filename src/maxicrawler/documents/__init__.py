"""Reading local documents into a format-independent representation."""

from maxicrawler.documents.loader import (
    DocumentError,
    DocumentLoader,
    UnsupportedDocumentError,
    default_readers,
)
from maxicrawler.documents.models import Document, DocumentType
from maxicrawler.documents.protocol import DocumentReader
from maxicrawler.documents.readers import (
    HtmlDocumentReader,
    MarkdownDocumentReader,
    TextDocumentReader,
)

__all__ = [
    "Document",
    "DocumentError",
    "DocumentLoader",
    "DocumentReader",
    "DocumentType",
    "HtmlDocumentReader",
    "MarkdownDocumentReader",
    "TextDocumentReader",
    "UnsupportedDocumentError",
    "default_readers",
]
