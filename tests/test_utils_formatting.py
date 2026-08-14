"""Tests for the one byte-size formatter both clients read from.

The terminal renderer had these assertions first and keeps them, because it
imports the same function. What is worth asserting *here* is that there is only
one of it: the command line and the web interface must not be able to disagree
about what 1.3 MB is.
"""

import pytest

from maxicrawler.cli import inspection
from maxicrawler.utils import SIZE_UNITS, UNKNOWN_SIZE, elide_middle, format_size, parse_size
from maxicrawler.utils.formatting import ELLIPSIS, SIZE_MULTIPLIERS
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


# --- reading one back ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "size"),
    [
        ("1000", 1000),
        ("0", 0),
        ("100 KB", 100_000),
        ("100KB", 100_000),
        ("100 kb", 100_000),
        ("1.3 MB", 1_300_000),
        ("1,3 MB", 1_300_000),
        ("2 GB", 2_000_000_000),
        ("  5 MB  ", 5_000_000),
        ("7 b", 7),
    ],
)
def test_a_size_is_read_back_from_what_a_person_types(text: str, size: int) -> None:
    assert parse_size(text) == size


def test_a_bare_number_is_bytes_rather_than_the_unit_of_the_box() -> None:
    """Guessing megabytes would be eight hundred thousand times wrong."""
    assert parse_size("500") == 500


@pytest.mark.parametrize("text", ["", "   ", "big", "10 furlongs", "1.2.3 MB", "-5 MB", "MB", None])
def test_anything_that_is_not_a_size_filters_nothing(text: str | None) -> None:
    """Lenient, because the value arrives in a query string."""
    assert parse_size(text) is None


@pytest.mark.parametrize("size", [0, 999, 1000, 1_300_000, 5_500_000_000])
def test_a_formatted_size_reads_back_as_itself(size: int) -> None:
    """The property the two boxes rely on: what the page prints, it accepts.

    True for every size this formatter prints exactly, which is every round one
    — and a bound is round. A size it has to round to one decimal comes back
    rounded, which is why the URL carries the byte count rather than the words.
    """
    assert parse_size(format_size(size)) == size


def test_the_two_directions_share_one_table_of_units() -> None:
    """Otherwise "KB" could mean one thing printed and another read."""
    assert tuple(SIZE_MULTIPLIERS) == SIZE_UNITS
    assert SIZE_MULTIPLIERS["MB"] == 1_000_000


# --- shortening a name so its ending survives ---------------------------------


def test_a_short_name_is_left_alone() -> None:
    assert elide_middle("holiday.jpg", 34) == "holiday.jpg"
    assert elide_middle("x" * 34, 34) == "x" * 34


def test_a_long_name_keeps_both_ends() -> None:
    """The extension is the point: it is what says what the file is."""
    shortened = elide_middle("a-very-long-holiday-photograph-name.jpeg", 20)

    assert len(shortened) == 20
    assert shortened.startswith("a-very-long-")
    assert shortened.endswith(".jpeg")
    assert ELLIPSIS in shortened


def test_a_limit_too_small_to_split_still_answers() -> None:
    """Nonsense in, something renderable out: never an exception, never longer."""
    assert elide_middle("holiday.jpg", 1) == ELLIPSIS
    assert len(elide_middle("holiday.jpg", 2)) == 2
    assert elide_middle("holiday.jpg", 0) == ELLIPSIS
