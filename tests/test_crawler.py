"""Tests for crawl planning."""

from maxicrawler.config import Settings
from maxicrawler.crawler import Crawler


def test_plan_normalizes_deduplicates_and_caps_urls() -> None:
    crawler = Crawler(Settings(max_pages=2))

    result = crawler.plan(
        [" https://example.test ", "", "https://example.test", "https://two.test"]
    )

    assert result.visited_urls == ("https://example.test", "https://two.test")
