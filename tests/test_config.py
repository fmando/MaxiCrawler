"""Tests for configuration validation."""

import pytest

from maxicrawler.config import Settings


def test_settings_rejects_invalid_page_limit() -> None:
    with pytest.raises(ValueError, match="at least one"):
        Settings(max_pages=0)
