"""Tests for the character encoding sniffing algorithm."""

import codecs

import pytest

from maxicrawler.web.encoding import (
    DEFAULT_ENCODING,
    PRESCAN_BYTES,
    decode_body,
    detect_encoding,
    normalize_label,
    sniff_bom,
    sniff_meta,
)


def test_a_utf8_bom_is_decisive() -> None:
    assert sniff_bom(codecs.BOM_UTF8 + b"<html>") == "utf-8-sig"


@pytest.mark.parametrize(
    ("mark", "expected"),
    [
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
        (codecs.BOM_UTF32_LE, "utf-32-le"),
        (codecs.BOM_UTF32_BE, "utf-32-be"),
    ],
)
def test_every_supported_bom_is_recognized(mark: bytes, expected: str) -> None:
    assert sniff_bom(mark + b"x") == expected


def test_a_utf32_le_bom_is_not_mistaken_for_a_utf16_one() -> None:
    assert sniff_bom(codecs.BOM_UTF32_LE + b"<") == "utf-32-le"


def test_no_bom_reports_nothing() -> None:
    assert sniff_bom(b"<html>") is None


def test_the_bom_outranks_the_header() -> None:
    body = codecs.BOM_UTF8 + b"<html>"

    assert detect_encoding(body, declared="iso-8859-1") == "utf-8-sig"


def test_the_header_outranks_the_markup() -> None:
    body = b'<meta charset="iso-8859-1">'

    assert detect_encoding(body, declared="utf-8") == "utf-8"


def test_the_markup_is_used_when_the_header_says_nothing() -> None:
    assert detect_encoding(b'<meta charset="iso-8859-1">') == "iso8859-1"


def test_a_meta_http_equiv_declaration_is_read() -> None:
    body = b'<meta http-equiv="Content-Type" content="text/html; charset=windows-1251">'

    assert detect_encoding(body) == "cp1251"


def test_an_unquoted_meta_charset_is_read() -> None:
    assert sniff_meta(b"<meta charset=utf-8>") == "utf-8"


def test_the_prescan_stops_after_the_first_kilobyte() -> None:
    body = b"<!-- " + b"x" * PRESCAN_BYTES + b" --><meta charset='iso-8859-1'>"

    assert sniff_meta(body) is None
    assert detect_encoding(body) == DEFAULT_ENCODING


def test_the_prescan_finds_a_declaration_just_inside_the_limit() -> None:
    padding = b"<!-- " + b"x" * (PRESCAN_BYTES - 60) + b" -->"
    body = padding + b"<meta charset='iso-8859-1'>"

    assert sniff_meta(body) == "iso8859-1"


@pytest.mark.parametrize("label", ["utf8", "UTF-8", " utf-8 ", '"utf-8"', "'UTF8'"])
def test_a_sloppy_but_usable_label_is_resolved(label: str) -> None:
    assert normalize_label(label) == "utf-8"


@pytest.mark.parametrize("label", [None, "", "   ", "unknown-9000", '""'])
def test_an_unusable_label_falls_through(label: str | None) -> None:
    assert normalize_label(label) is None


def test_an_unusable_header_label_falls_through_to_the_markup() -> None:
    body = b'<meta charset="iso-8859-1">'

    assert detect_encoding(body, declared="unknown-9000") == "iso8859-1"


def test_nothing_declared_falls_back_to_utf8() -> None:
    assert detect_encoding(b"<html><body>hello</body></html>") == DEFAULT_ENCODING


def test_a_declared_body_round_trips() -> None:
    text, encoding = decode_body("Käse".encode("iso-8859-1"), declared="iso-8859-1")

    assert text == "Käse"
    assert encoding == "iso8859-1"


def test_a_utf8_bom_is_stripped_from_the_text() -> None:
    text, encoding = decode_body(codecs.BOM_UTF8 + "Käse".encode())

    assert text == "Käse"
    assert encoding == "utf-8-sig"


def test_undecodable_bytes_are_replaced_rather_than_raised() -> None:
    text, encoding = decode_body(b"caf\xe9 \xff\xfe", declared="utf-8")

    assert encoding == "utf-8"
    assert "caf" in text
    assert "�" in text


def test_a_lying_declaration_still_produces_text() -> None:
    text, _ = decode_body("Käse".encode("iso-8859-1"), declared="utf-8")

    assert "K" in text
