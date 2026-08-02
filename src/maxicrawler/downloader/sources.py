"""What the user actually handed to the download command.

There is one ``download`` command and one argument, because from the outside
the distinction between "a link" and "a file full of links" is not interesting:
in both cases the answer to *"what should I download?"* is a list of URLs. This
module is where that distinction is resolved, once, so nothing downstream has
to care.

Reading documents reuses the discovery machinery — the same readers, the same
extractor, the same rules about what counts as a URL — rather than a second,
subtly different scanner. Whatever ``maxicrawler discover`` finds in a file is
exactly what ``maxicrawler download`` will try to fetch from it.
"""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from maxicrawler.documents import DocumentLoader
from maxicrawler.downloader.errors import SourceError
from maxicrawler.extractors import GenericUrlExtractor, UrlExtractor


@dataclass(frozen=True, slots=True)
class SourceItem:
    """One URL a source yielded, and where it came from."""

    url: str
    origin: str | None = None
    """The document the URL was read from; ``None`` for a URL given directly."""


def looks_like_url(value: str) -> bool:
    """Return whether *value* is meant as an HTTP(S) URL rather than a path.

    A Windows path such as ``C:\\links.txt`` parses as a URL with the scheme
    ``c``, so the scheme is checked against the two that matter instead of
    merely being required to exist.
    """
    parsed = urlsplit(value.strip())
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


class SourceResolver:
    """Turns one download source into the URLs it stands for."""

    def __init__(
        self,
        *,
        loader: DocumentLoader | None = None,
        extractor: UrlExtractor | None = None,
    ) -> None:
        self._loader = loader if loader is not None else DocumentLoader()
        self._extractor = extractor if extractor is not None else GenericUrlExtractor()

    @property
    def loader(self) -> DocumentLoader:
        """Return the document loader in use."""
        return self._loader

    def resolve(self, source: str) -> tuple[SourceItem, ...]:
        """Return the URLs *source* stands for.

        A URL stands for itself. A file or a directory is read for the URLs it
        contains, recursively and in sorted order, so a run over a directory is
        reproducible.

        Duplicates are removed across the whole source: the same link written
        in two documents is downloaded once, and the first occurrence keeps its
        origin.

        Raises:
            SourceError: *source* is neither an HTTP(S) URL nor a readable
                path.
        """
        text = source.strip()
        if not text:
            msg = "no download source was given"
            raise SourceError(msg)
        if looks_like_url(text):
            return (SourceItem(url=text),)
        path = Path(text)
        if not path.exists():
            msg = f"neither an HTTP(S) URL nor an existing path: {source}"
            raise SourceError(msg)
        if path.is_file() and not self._loader.supports(path):
            supported = ", ".join(sorted(self._loader.supported_suffixes))
            msg = f"unsupported document type: {source} (supported: {supported})"
            raise SourceError(msg)
        return self._from_documents(path)

    def _from_documents(self, root: Path) -> tuple[SourceItem, ...]:
        """Return the unique URLs found at or below *root*."""
        seen: set[str] = set()
        items: list[SourceItem] = []
        for document in self._loader.load_all(root):
            for candidate in self._extractor.extract(document):
                if candidate.normalized_url in seen:
                    continue
                seen.add(candidate.normalized_url)
                items.append(SourceItem(url=candidate.raw_url, origin=document.source))
        return tuple(items)
