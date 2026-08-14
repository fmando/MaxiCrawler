"""Tests for the one table that decides what a browser is shown.

Pure functions over a literal table, so every case is a line. The two tests
worth reading twice are the one asserting Markdown is plain text and the one
asserting `mimetypes` is never consulted: the first is the whole "do not
convert" rule, the second is what keeps a content type from depending on which
machine answered the request.
"""

import ast
from pathlib import Path

import pytest

from maxicrawler.app.viewing import (
    DEFAULT_MAX_VIEW_BYTES,
    DOWNLOAD_CONTENT_TYPE,
    HTML,
    KINDS,
    PLAIN_TEXT,
    SVG,
    VIEWABLE,
    Display,
    MediaKind,
    kind_for,
    verdict_for,
)


@pytest.mark.parametrize(
    ("filename", "content_type", "display"),
    [
        ("Jump.pdf", "application/pdf", Display.IFRAME),
        ("photo.png", "image/png", Display.IMAGE),
        ("photo.JPG", "image/jpeg", Display.IMAGE),
        ("photo.jpeg", "image/jpeg", Display.IMAGE),
        ("animation.gif", "image/gif", Display.IMAGE),
        ("modern.webp", "image/webp", Display.IMAGE),
        ("drawing.svg", SVG, Display.IMAGE),
        ("notes.txt", PLAIN_TEXT, Display.IFRAME),
        ("server.log", PLAIN_TEXT, Display.IFRAME),
        ("data.csv", PLAIN_TEXT, Display.IFRAME),
        ("payload.json", PLAIN_TEXT, Display.IFRAME),
        ("feed.xml", PLAIN_TEXT, Display.IFRAME),
        ("page.html", HTML, Display.IFRAME),
        ("page.HTM", HTML, Display.IFRAME),
    ],
)
def test_what_a_browser_is_asked_to_show(
    filename: str, content_type: str, display: Display
) -> None:
    verdict = verdict_for(filename, size=10)

    assert verdict.content_type == content_type
    assert verdict.display is display
    assert verdict.can_display is True
    assert verdict.reason is None


def test_markdown_is_served_as_plain_text() -> None:
    """No browser renders Markdown, and rendering it here would be converting it."""
    for name in ("readme.md", "NOTES.markdown"):
        verdict = verdict_for(name, size=10)

        assert verdict.content_type == PLAIN_TEXT
        assert "markdown" not in verdict.content_type


@pytest.mark.parametrize("filename", ["ubuntu.iso", "archive.zip", "binary", "a.tar.gz", "x.exe"])
def test_an_unknown_type_is_a_download_with_a_reason(filename: str) -> None:
    verdict = verdict_for(filename, size=10)

    assert verdict.display is Display.NONE
    assert verdict.can_display is False
    assert verdict.content_type == DOWNLOAD_CONTENT_TYPE
    assert "can show" in (verdict.reason or "")


def test_a_file_with_no_extension_says_so() -> None:
    assert "no extension" in (verdict_for("content", size=1).reason or "")


def test_a_known_type_that_is_too_large_keeps_its_type_and_loses_its_display() -> None:
    """So a page can say "a PDF, too large" rather than "unknown file"."""
    verdict = verdict_for("Jump.pdf", size=DEFAULT_MAX_VIEW_BYTES + 1)

    assert verdict.content_type == "application/pdf"
    assert verdict.display is Display.NONE
    assert "above the viewer's" in (verdict.reason or "")


def test_the_limit_is_the_caller_s_to_choose() -> None:
    assert verdict_for("notes.txt", size=100, max_bytes=99).can_display is False
    assert verdict_for("notes.txt", size=100, max_bytes=100).can_display is True


def test_a_size_nobody_measured_is_shown() -> None:
    """Refusing a file because nobody measured it would be the wrong way round."""
    assert verdict_for("notes.txt").can_display is True


# --- the two types a browser fetches in pieces --------------------------------


@pytest.mark.parametrize(
    ("filename", "content_type", "display"),
    [
        ("clip.mp4", "video/mp4", Display.VIDEO),
        ("clip.M4V", "video/mp4", Display.VIDEO),
        ("clip.webm", "video/webm", Display.VIDEO),
        ("clip.mov", "video/quicktime", Display.VIDEO),
        ("song.mp3", "audio/mpeg", Display.AUDIO),
        ("song.flac", "audio/flac", Display.AUDIO),
        ("song.opus", "audio/ogg", Display.AUDIO),
        ("song.wav", "audio/wav", Display.AUDIO),
    ],
)
def test_what_a_browser_is_asked_to_play(
    filename: str, content_type: str, display: Display
) -> None:
    verdict = verdict_for(filename, size=10)

    assert verdict.content_type == content_type
    assert verdict.display is display
    assert verdict.can_display is True


@pytest.mark.parametrize("filename", ["clip.mkv", "clip.avi", "clip.wmv", "song.wma", "song.mid"])
def test_a_container_no_browser_plays_is_a_download(filename: str) -> None:
    """A player showing a black rectangle is worse than a download link."""
    assert verdict_for(filename, size=10).display is Display.NONE


def test_a_recording_is_not_bounded_by_the_ceiling_for_documents() -> None:
    """It arrives in ranges, so the reason that ceiling exists does not reach it."""
    verdict = verdict_for("clip.mp4", size=DEFAULT_MAX_VIEW_BYTES * 100)

    assert verdict.display is Display.VIDEO
    assert verdict.can_display is True


def test_a_recording_has_a_ceiling_of_its_own_when_one_is_set() -> None:
    assert verdict_for("clip.mp4", size=100, max_stream_bytes=99).can_display is False
    assert verdict_for("clip.mp4", size=100, max_stream_bytes=100).can_display is True


def test_the_streaming_ceiling_does_not_reach_a_document() -> None:
    """Two limits, two reasons; neither is allowed to answer for the other."""
    assert verdict_for("notes.txt", size=1_000_000, max_stream_bytes=1).can_display is True
    assert verdict_for("Jump.pdf", size=1_000_000, max_stream_bytes=1).can_display is True


def test_nothing_that_streams_can_execute_script_in_our_origin() -> None:
    assert verdict_for("clip.mp4", size=1).is_script_capable is False
    assert verdict_for("song.mp3", size=1).is_script_capable is False


# --- the two types that are executable code -----------------------------------


def test_html_and_svg_are_the_script_capable_ones() -> None:
    assert verdict_for("page.html", size=1).is_script_capable is True
    assert verdict_for("drawing.svg", size=1).is_script_capable is True


@pytest.mark.parametrize("filename", ["Jump.pdf", "photo.png", "notes.txt", "ubuntu.iso"])
def test_nothing_else_is(filename: str) -> None:
    """A PDF may hold script, but it runs in the viewer, not in our origin."""
    assert verdict_for(filename, size=1).is_script_capable is False


def test_svg_is_never_framed() -> None:
    """An `<img>` runs no script even when the file it points at contains some."""
    assert verdict_for("drawing.svg", size=1).display is Display.IMAGE


# --- what sort of file it is ---------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "kind"),
    [
        ("holiday.JPG", MediaKind.IMAGE),
        ("drawing.svg", MediaKind.IMAGE),
        ("talk.mp4", MediaKind.VIDEO),
        ("song.flac", MediaKind.AUDIO),
        ("manual.pdf", MediaKind.PDF),
        ("notes.docx", MediaKind.DOCUMENT),
        ("page.html", MediaKind.DOCUMENT),
        ("release.tar.gz", MediaKind.ARCHIVE),
        ("disk.iso", MediaKind.ARCHIVE),
        ("readme.md", MediaKind.TEXT),
        ("links.txt", MediaKind.TEXT),
        ("installer.exe", MediaKind.OTHER),
        ("README", MediaKind.OTHER),
    ],
)
def test_a_file_is_categorised_by_its_suffix(filename: str, kind: MediaKind) -> None:
    assert kind_for(filename) is kind


def test_a_file_nobody_named_is_other_rather_than_an_error() -> None:
    assert kind_for(None) is MediaKind.OTHER
    assert kind_for("") is MediaKind.OTHER


def test_a_category_is_lenient_about_what_a_query_string_carries() -> None:
    assert MediaKind.parse("image") is MediaKind.IMAGE
    assert MediaKind.parse("sculpture") is None
    assert MediaKind.parse(None) is None


def test_every_suffix_a_browser_may_be_shown_also_has_a_category() -> None:
    """Otherwise a file the viewer renders sits under "other" in the filter.

    The reverse does not hold and is the point of two tables: `KINDS` covers
    video and archives, which `VIEWABLE` deliberately does not.
    """
    assert set(VIEWABLE) <= set(KINDS)


def test_categorising_a_file_never_reads_it() -> None:
    """A category is a hint for sorting, not a claim about content.

    Opening a thousand files to fill a listing would cost more than the sorting
    saves — and would make the answer depend on whether the payload is still
    there, which for a refused or failed entry it is not.
    """
    assert kind_for("holiday.jpg") is MediaKind.IMAGE


# --- the table itself ---------------------------------------------------------


def test_every_suffix_in_the_category_table_is_lower_case_and_dotted() -> None:
    for suffix in KINDS:
        assert suffix.startswith(".")
        assert suffix == suffix.lower()


def test_every_suffix_in_the_table_is_lower_case_and_dotted() -> None:
    for suffix in VIEWABLE:
        assert suffix.startswith(".")
        assert suffix == suffix.lower()


def test_the_table_declares_a_charset_for_every_text_type() -> None:
    """Otherwise the browser guesses, and guesses differently per platform."""
    for content_type, _ in VIEWABLE.values():
        if content_type.startswith("text/"):
            assert "charset=" in content_type


def test_the_content_type_never_depends_on_the_machine() -> None:
    """`mimetypes` reads the Windows registry; a content type must not.

    Read from the syntax tree rather than by calling anything, so the assertion
    holds for the module as written rather than for one code path through it.
    """
    source = Path("src/maxicrawler/app/viewing.py").read_text(encoding="utf-8")
    imported = {
        name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import | ast.ImportFrom)
        for name in ([alias.name for alias in node.names] + [node.module or ""])
    }

    assert "mimetypes" not in imported
