"""Tests for the one byte-size formatter both clients read from.

The terminal renderer had these assertions first and keeps them, because it
imports the same function. What is worth asserting *here* is that there is only
one of it: the command line and the web interface must not be able to disagree
about what 1.3 MB is.
"""

import pytest

from maxicrawler.cli import inspection
from maxicrawler.utils import UNKNOWN_SIZE, format_size
from maxicrawler.utils.formatting import format_size as canonical


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (0, "0 B"),
        (1, "1 B"),
        (999, "999 B"),
        (1000, "1.0 KB"),
        (1_300_000, "1.3 MB"),
        (2_800_000, "2.8 MB"),
        (5_500_000_000, "5.5 GB"),
        (10**18, "1000.0 PB"),
    ],
)
def test_a_size_reads_the_way_a_provider_advertises_it(size: int, text: str) -> None:
    assert format_size(size) == text


def test_an_absent_size_is_unknown_rather_than_zero() -> None:
    """A provider that stated no length is not a payload of no bytes."""
    assert format_size(None) == UNKNOWN_SIZE
    assert format_size(0) != UNKNOWN_SIZE


def test_the_terminal_renderer_uses_the_shared_function() -> None:
    """Not a tautology: it is what keeps a second copy from appearing here."""
    assert inspection.format_size is canonical
