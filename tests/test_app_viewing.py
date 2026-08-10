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
    PLAIN_TEXT,
    SVG,
    VIEWABLE,
    Display,
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


# --- the table itself ---------------------------------------------------------


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
