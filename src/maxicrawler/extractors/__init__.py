"""Extraction of structured content from documents."""

from maxicrawler.extractors.urls import (
    GenericUrlExtractor,
    UrlCandidate,
    UrlExtractor,
    scan_text,
)

__all__ = ["GenericUrlExtractor", "UrlCandidate", "UrlExtractor", "scan_text"]
