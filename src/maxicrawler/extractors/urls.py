"""Extraction of URL candidates from documents of any supported format."""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import chain
from typing import Protocol, runtime_checkable

from maxicrawler.documents import Document
from maxicrawler.utils import normalize_url

URL_PATTERN = re.compile(r"https?://[^\s<>\"'`)\]}]+", re.IGNORECASE)
"""Matches absolute HTTP(S) URLs and stops at markup and whitespace delimiters."""

TRAILING_PUNCTUATION = ".,;:!?\"'"
"""Characters stripped from the end of a match; prose punctuation, not URL syntax."""


@dataclass(frozen=True, slots=True)
class UrlCandidate:
    """A URL found in a document, in both its original and canonical form."""

    raw_url: str
    normalized_url: str


@runtime_checkable
class UrlExtractor(Protocol):
    """Turns a document into the URL candidates it contains."""

    def extract(self, document: Document) -> tuple[UrlCandidate, ...]:
        """Return the candidates found in *document*, in a deterministic order."""
        ...


class GenericUrlExtractor:
    """Extracts absolute HTTP(S) URLs from any supported document format.

    Format differences are already resolved by the reader layer, so one
    implementation serves plain text, Markdown and HTML alike: markup link
    targets are taken verbatim from :attr:`Document.links` and prose is scanned
    with a regular expression.

    Candidates are validated with :func:`maxicrawler.utils.normalize_url`, so
    malformed, relative and non-HTTP(S) URLs are skipped. Duplicates *within
    one document* are removed, because a single element can yield the same URL
    twice — an ``<a href="x">x</a>`` contributes both a link target and visible
    text. Duplicates *across* documents are deliberately preserved and left to
    :class:`~maxicrawler.crawler.DiscoveryPipeline`, which counts them.
    """

    def extract(self, document: Document) -> tuple[UrlCandidate, ...]:
        """Return the unique candidates in *document*, markup links first."""
        seen: set[str] = set()
        candidates: list[UrlCandidate] = []
        for raw_url in chain(document.links, self._scan(document.text)):
            candidate = self._to_candidate(raw_url)
            if candidate is None or candidate.normalized_url in seen:
                continue
            seen.add(candidate.normalized_url)
            candidates.append(candidate)
        return tuple(candidates)

    @staticmethod
    def _scan(text: str) -> Iterator[str]:
        """Yield every URL-shaped substring of *text*."""
        for match in URL_PATTERN.finditer(text):
            yield match.group()

    @staticmethod
    def _to_candidate(raw_url: str) -> UrlCandidate | None:
        """Return a candidate for *raw_url*, or ``None`` when it is unusable."""
        cleaned = raw_url.strip().rstrip(TRAILING_PUNCTUATION)
        if not cleaned:
            return None
        try:
            normalized_url = normalize_url(cleaned)
        except ValueError:
            return None
        return UrlCandidate(raw_url=cleaned, normalized_url=normalized_url)
