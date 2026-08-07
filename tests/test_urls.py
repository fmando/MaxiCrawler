"""Tests for URL normalization, redaction, and duplicate detection."""

import pytest

from maxicrawler.utils import DuplicateDetector, normalize_url, require_http_scheme, safe_target


def test_normalize_url_canonicalizes_host_and_query() -> None:
    normalized = normalize_url("HTTPS://Example.TEST:443/path?b=2&a=1")

    assert normalized == "https://example.test/path?a=1&b=2"


def test_normalize_url_preserves_the_fragment() -> None:
    normalized = normalize_url("HTTPS://Example.TEST/path#Section")

    assert normalized == "https://example.test/path#Section"


def test_normalize_url_keeps_fragment_case_and_order() -> None:
    key = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"

    assert normalize_url(f"https://mega.nz/file/AaBbCcDd#{key}").endswith(f"#{key}")


def test_normalize_url_distinguishes_links_that_differ_only_in_the_fragment() -> None:
    first = normalize_url("https://mega.nz/#!AaBbCcDd!0123456789abcdefghijklmnopq")
    second = normalize_url("https://mega.nz/#!ZzYyXxWw!0123456789abcdefghijklmnopq")

    assert first != second


def test_normalize_url_without_a_fragment_is_unchanged() -> None:
    assert normalize_url("https://example.test/path") == "https://example.test/path"


def test_normalize_url_treats_an_empty_fragment_as_absent() -> None:
    assert normalize_url("https://example.test/path#") == "https://example.test/path"


def test_normalize_url_rejects_non_http_urls() -> None:
    with pytest.raises(ValueError, match="absolute HTTP"):
        normalize_url("/relative")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://example.test/", "http"),
        ("HTTPS://Example.TEST/", "https"),
        ("https://example.test/a?b=1#c", "https"),
    ],
)
def test_require_http_scheme_accepts_http_and_https(url: str, expected: str) -> None:
    assert require_http_scheme(url) == expected


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://example.test/x", "data:text/html,x", "javascript:alert(1)"],
)
def test_require_http_scheme_refuses_every_other_scheme(url: str) -> None:
    with pytest.raises(ValueError, match="unsupported URL scheme"):
        require_http_scheme(url)


def test_require_http_scheme_names_a_missing_scheme() -> None:
    with pytest.raises(ValueError, match=r"unsupported URL scheme: \(none\)"):
        require_http_scheme("/relative/path")


def test_safe_target_keeps_scheme_host_and_path() -> None:
    assert safe_target("https://example.test:8443/a/b") == "https://example.test:8443/a/b"


def test_safe_target_drops_the_query_and_the_fragment() -> None:
    target = safe_target("https://mega.nz/file/AaBbCcDd?n=Handle#SecretKey")

    assert target == "https://mega.nz/file/AaBbCcDd"
    assert "SecretKey" not in target
    assert "Handle" not in target


def test_duplicate_detector_registers_only_once() -> None:
    detector = DuplicateDetector()

    assert detector.register("https://example.test/") is False
    assert detector.register("https://example.test/") is True


def test_duplicate_detector_separates_different_fragments() -> None:
    detector = DuplicateDetector()

    assert detector.register(normalize_url("https://mega.nz/#!AaBbCcDd!key")) is False
    assert detector.register(normalize_url("https://mega.nz/#!ZzYyXxWw!key")) is False
