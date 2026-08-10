"""Tests for what a URL says it points at.

Pure functions over strings, so nothing here needs a network, a file or a crawl.
"""

import pytest

from maxicrawler.app import TARGETS, TargetKind, target_of
from maxicrawler.app.targets import suffix_of


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.test/report.pdf", TargetKind.DOCUMENT),
        ("https://example.test/notes.MD", TargetKind.DOCUMENT),
        ("https://example.test/photo.jpg", TargetKind.IMAGE),
        ("https://example.test/logo.SVG", TargetKind.IMAGE),
        ("https://example.test/backup.zip", TargetKind.ARCHIVE),
        ("https://example.test/disc.iso", TargetKind.ARCHIVE),
        ("https://example.test/clip.mp4", TargetKind.VIDEO),
        ("https://example.test/song.flac", TargetKind.AUDIO),
        ("https://example.test/index.html", TargetKind.PAGE),
    ],
)
def test_a_suffix_names_what_a_url_points_at(url: str, expected: TargetKind) -> None:
    assert target_of(url) is expected


def test_a_url_with_no_suffix_does_not_claim_to_be_a_page() -> None:
    """Most pages have no suffix, so guessing would be trusted and often wrong."""
    assert target_of("https://example.test/articles/holiday") is TargetKind.UNKNOWN
    assert target_of("https://example.test/") is TargetKind.UNKNOWN


def test_an_unknown_suffix_is_unknown_rather_than_guessed_at() -> None:
    assert target_of("https://example.test/thing.wibble") is TargetKind.UNKNOWN


def test_a_suffix_that_would_be_wrong_half_the_time_earns_no_entry() -> None:
    """`.ts` is a transport stream and it is TypeScript; `.dat` is anything."""
    assert target_of("https://example.test/stream.ts") is TargetKind.UNKNOWN
    assert target_of("https://example.test/blob.dat") is TargetKind.UNKNOWN


def test_how_a_page_is_produced_is_not_what_it_returns() -> None:
    assert target_of("https://example.test/index.php") is TargetKind.UNKNOWN
    assert target_of("https://example.test/default.aspx") is TargetKind.UNKNOWN


# --- what must never decide the answer ---------------------------------------


def test_a_query_string_does_not_decide_what_this_url_is() -> None:
    """`?next=/a.pdf` names a redirect target, not this URL's own content."""
    assert target_of("https://example.test/go?next=/a.pdf") is TargetKind.UNKNOWN


def test_a_fragment_never_decides_anything() -> None:
    """A share link keeps its decryption key there, and a key is random characters.

    Forty of them will eventually spell `.png`, and a report that then filed the
    share under images would be wrong once in a few hundred links -- which is
    exactly often enough to be believed.
    """
    key = "https://mega.nz/file/AaBbCcDd#0123456789abcdef.png"

    assert target_of(key) is TargetKind.UNKNOWN


def test_a_fragment_cannot_hide_a_real_suffix_either() -> None:
    assert target_of("https://example.test/report.pdf#page=4") is TargetKind.DOCUMENT


# --- reading the path --------------------------------------------------------


def test_percent_encoding_is_undone_before_the_suffix_is_taken() -> None:
    """`%2E` is a dot to every server that will answer this URL."""
    assert target_of("https://example.test/report%2Epdf") is TargetKind.DOCUMENT


def test_a_space_in_the_name_changes_nothing() -> None:
    assert target_of("https://example.test/my%20report.pdf") is TargetKind.DOCUMENT


def test_the_last_suffix_wins() -> None:
    assert target_of("https://example.test/archive.tar.gz") is TargetKind.ARCHIVE


def test_a_dot_in_a_directory_is_not_the_files_suffix() -> None:
    assert suffix_of("https://example.test/v1.2/download") == ""


def test_a_url_that_cannot_be_read_names_no_suffix() -> None:
    assert target_of("https://[not-an-address]/a.pdf") is TargetKind.UNKNOWN


def test_surrounding_space_is_ignored() -> None:
    assert target_of("  https://example.test/a.png  ") is TargetKind.IMAGE


def test_something_that_is_not_a_web_url_is_read_leniently() -> None:
    """Discovery records what it was given; classifying it must not raise."""
    assert target_of("mailto:someone@example.test") is TargetKind.UNKNOWN
    assert target_of("") is TargetKind.UNKNOWN


# --- the table itself --------------------------------------------------------


def test_every_suffix_is_written_the_way_it_is_looked_up() -> None:
    """Lower case and leading dot, or the lookup silently never matches."""
    for suffix in TARGETS:
        assert suffix == suffix.lower()
        assert suffix.startswith(".")
        assert suffix.count(".") == 1


def test_nothing_in_the_table_claims_to_be_unknown() -> None:
    assert TargetKind.UNKNOWN not in set(TARGETS.values())


def test_every_kind_but_unknown_has_at_least_one_suffix() -> None:
    """A kind nothing can produce is a filter entry that can only disappoint."""
    produced = set(TARGETS.values())

    assert produced == set(TargetKind) - {TargetKind.UNKNOWN}
