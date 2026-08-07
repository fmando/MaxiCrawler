"""Tests for the crawl frontier and the visited set."""

import pytest

from maxicrawler.web.frontier import (
    CrawlItem,
    FifoFrontier,
    Frontier,
    InMemoryVisitedSet,
    VisitedSet,
    visit_key,
)

MEGA_KEY = "0123456789abcdefghijklmnopqrstuvwxyzABC"


def item(url: str, depth: int = 0, source: str | None = None) -> CrawlItem:
    """Return a crawl item for *url*."""
    return CrawlItem(url=url, depth=depth, discovered_from=source)


# --- the visit key -----------------------------------------------------------


def test_the_visit_key_drops_the_fragment() -> None:
    assert visit_key("https://example.test/page#intro") == "https://example.test/page"


def test_two_anchors_into_one_page_share_a_visit_key() -> None:
    """A navigation menu of anchors must not become a page each."""
    first = visit_key("https://example.test/page#intro")
    second = visit_key("https://example.test/page#setup")

    assert first == second


def test_the_visit_key_canonicalizes_host_port_and_query() -> None:
    key = visit_key("HTTPS://Example.TEST:443/page?b=2&a=1")

    assert key == "https://example.test/page?a=1&b=2"


def test_the_visit_key_differs_from_the_discovery_key_for_a_mega_link() -> None:
    """Discovery keeps the fragment because it is the link's identity.

    The frontier drops it because it is not a different page. Sharing one key
    would either refetch a page per anchor or destroy the Mega link.
    """
    from maxicrawler.utils import normalize_url

    url = f"https://mega.nz/file/AaBbCcDd#{MEGA_KEY}"

    assert MEGA_KEY in normalize_url(url)
    assert MEGA_KEY not in visit_key(url)


def test_the_visit_key_refuses_a_url_it_cannot_canonicalize() -> None:
    with pytest.raises(ValueError, match="absolute HTTP"):
        visit_key("mailto:someone@example.test")


# --- the visited set ---------------------------------------------------------


def test_the_visited_set_satisfies_the_runtime_protocol() -> None:
    assert isinstance(InMemoryVisitedSet(), VisitedSet)


def test_registering_reports_whether_the_key_was_new() -> None:
    visited = InMemoryVisitedSet()

    assert visited.register("https://example.test/a") is True
    assert visited.register("https://example.test/a") is False


def test_a_registered_key_is_contained() -> None:
    visited = InMemoryVisitedSet()
    visited.register("https://example.test/a")

    assert "https://example.test/a" in visited
    assert "https://example.test/b" not in visited


def test_the_visited_set_can_start_from_known_keys() -> None:
    visited = InMemoryVisitedSet(["https://example.test/a"])

    assert visited.register("https://example.test/a") is False
    assert len(visited) == 1


def test_the_visited_set_counts_what_it_knows() -> None:
    visited = InMemoryVisitedSet()
    visited.register("https://example.test/a")
    visited.register("https://example.test/b")
    visited.register("https://example.test/a")

    assert len(visited) == 2


def test_the_visited_set_repr_names_only_its_size() -> None:
    visited = InMemoryVisitedSet(["https://example.test/secret?token=abc"])

    assert "token" not in repr(visited)
    assert "known=1" in repr(visited)


# --- the frontier ------------------------------------------------------------


def test_the_frontier_satisfies_the_runtime_protocol() -> None:
    assert isinstance(FifoFrontier(), Frontier)


def test_an_empty_frontier_hands_out_nothing() -> None:
    frontier = FifoFrontier()

    assert frontier.pop() is None
    assert frontier.pending == 0
    assert bool(frontier) is False


def test_items_come_out_in_the_order_they_went_in() -> None:
    frontier = FifoFrontier()
    for path in ("/a", "/b", "/c"):
        frontier.push(item(f"https://example.test{path}"))

    popped = [frontier.pop(), frontier.pop(), frontier.pop()]

    assert [entry.url for entry in popped if entry] == [
        "https://example.test/a",
        "https://example.test/b",
        "https://example.test/c",
    ]


def test_breadth_first_means_a_whole_depth_before_the_next() -> None:
    frontier = FifoFrontier()
    frontier.push(item("https://example.test/a", depth=1))
    frontier.push(item("https://example.test/b", depth=1))
    first = frontier.pop()
    assert first is not None
    frontier.push(item("https://example.test/a1", depth=2))

    remaining = [frontier.pop(), frontier.pop()]

    assert [entry.depth for entry in remaining if entry] == [1, 2]


def test_the_frontier_does_not_deduplicate() -> None:
    """Identity is the visited set's job, not the queue's.

    A queue that also owned identity could not later be swapped for one that
    owns only order, which is what a priority or distributed frontier is.
    """
    frontier = FifoFrontier()
    frontier.push(item("https://example.test/a"))
    frontier.push(item("https://example.test/a"))

    assert frontier.pending == 2


def test_the_frontier_carries_depth_and_origin() -> None:
    frontier = FifoFrontier()
    frontier.push(item("https://example.test/b", depth=2, source="https://example.test/a"))

    popped = frontier.pop()

    assert popped is not None
    assert popped.depth == 2
    assert popped.discovered_from == "https://example.test/a"


def test_a_frontier_can_start_from_known_items() -> None:
    frontier = FifoFrontier([item("https://example.test/a")])

    assert frontier.pending == 1


def test_the_frontier_counts_what_is_waiting() -> None:
    frontier = FifoFrontier()
    frontier.push(item("https://example.test/a"))
    frontier.push(item("https://example.test/b"))

    assert len(frontier) == 2
    assert bool(frontier) is True
    frontier.pop()
    assert frontier.pending == 1


def test_the_frontier_repr_names_only_its_size() -> None:
    frontier = FifoFrontier([item("https://example.test/a?token=secret")])

    assert "token" not in repr(frontier)
    assert "pending=1" in repr(frontier)


def test_a_crawl_item_is_immutable() -> None:
    entry = item("https://example.test/a")

    with pytest.raises(AttributeError):
        entry.depth = 1  # type: ignore[misc]
