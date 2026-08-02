"""Tests for URL normalization and duplicate detection."""

import pytest

from maxicrawler.utils import DuplicateDetector, normalize_url


def test_normalize_url_canonicalizes_host_query_and_fragment() -> None:
    normalized = normalize_url("HTTPS://Example.TEST:443/path?b=2&a=1#section")

    assert normalized == "https://example.test/path?a=1&b=2"


def test_normalize_url_rejects_non_http_urls() -> None:
    with pytest.raises(ValueError, match="absolute HTTP"):
        normalize_url("/relative")


def test_duplicate_detector_registers_only_once() -> None:
    detector = DuplicateDetector()

    assert detector.register("https://example.test/") is False
    assert detector.register("https://example.test/") is True
