"""Tests for immutable domain models."""

from dataclasses import FrozenInstanceError

import pytest

from maxicrawler.domain import Statistics, UrlRecord


def test_url_record_is_immutable() -> None:
    record = UrlRecord("https://example.test", "https://example.test/")

    with pytest.raises(FrozenInstanceError):
        record.raw_url = "https://other.test"  # type: ignore[misc]


def test_statistics_returns_new_values() -> None:
    initial = Statistics()

    discovered = initial.with_discovery(duplicate=False)
    duplicate = discovered.with_discovery(duplicate=True)

    assert initial.discovered_urls == 0
    assert discovered.discovered_urls == 1
    assert duplicate.duplicate_urls == 1
