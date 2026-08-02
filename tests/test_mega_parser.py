"""Tests for Mega URL recognition."""

import pytest

from maxicrawler.plugins.mega import MegaLinkFormat, MegaLinkKind, parse_mega_url

FILE_KEY = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
FOLDER_KEY = "0123456789abcdefghijkl"
HANDLE = "AaBbCcDd"
NODE = "N0d3H4nd"


def test_file_key_and_folder_key_have_the_documented_lengths() -> None:
    assert len(FILE_KEY) == 43
    assert len(FOLDER_KEY) == 22
    assert len(HANDLE) == 8


def test_modern_file_link() -> None:
    link = parse_mega_url(f"https://mega.nz/file/{HANDLE}#{FILE_KEY}")

    assert link is not None
    assert link.kind is MegaLinkKind.FILE
    assert link.link_format is MegaLinkFormat.MODERN
    assert link.handle == HANDLE
    assert link.key == FILE_KEY
    assert link.has_key is True
    assert link.selects_node is False


def test_modern_folder_link() -> None:
    link = parse_mega_url(f"https://mega.nz/folder/{HANDLE}#{FOLDER_KEY}")

    assert link is not None
    assert link.kind is MegaLinkKind.FOLDER
    assert link.key == FOLDER_KEY
    assert link.node_handle is None


def test_modern_folder_link_selecting_a_file() -> None:
    link = parse_mega_url(f"https://mega.nz/folder/{HANDLE}#{FOLDER_KEY}/file/{NODE}")

    assert link is not None
    assert link.kind is MegaLinkKind.FOLDER
    assert link.node_handle == NODE
    assert link.node_kind is MegaLinkKind.FILE
    assert link.selects_node is True


def test_modern_folder_link_selecting_a_subfolder() -> None:
    link = parse_mega_url(f"https://mega.nz/folder/{HANDLE}#{FOLDER_KEY}/folder/{NODE}")

    assert link is not None
    assert link.node_kind is MegaLinkKind.FOLDER


def test_modern_link_without_a_key_is_still_recognized() -> None:
    link = parse_mega_url(f"https://mega.nz/file/{HANDLE}")

    assert link is not None
    assert link.kind is MegaLinkKind.FILE
    assert link.has_key is False


def test_modern_link_with_an_unreadable_fragment_keeps_its_identity() -> None:
    link = parse_mega_url(f"https://mega.nz/file/{HANDLE}#short")

    assert link is not None
    assert link.handle == HANDLE
    assert link.has_key is False


def test_legacy_file_link() -> None:
    link = parse_mega_url(f"https://mega.nz/#!{HANDLE}!{FILE_KEY}")

    assert link is not None
    assert link.kind is MegaLinkKind.FILE
    assert link.link_format is MegaLinkFormat.LEGACY
    assert link.handle == HANDLE
    assert link.key == FILE_KEY


def test_legacy_folder_link() -> None:
    link = parse_mega_url(f"https://mega.nz/#F!{HANDLE}!{FOLDER_KEY}")

    assert link is not None
    assert link.kind is MegaLinkKind.FOLDER
    assert link.key == FOLDER_KEY


def test_legacy_folder_link_selecting_a_node() -> None:
    link = parse_mega_url(f"https://mega.nz/#F!{HANDLE}!{FOLDER_KEY}!{NODE}")

    assert link is not None
    assert link.kind is MegaLinkKind.FOLDER
    assert link.node_handle == NODE
    assert link.node_kind is None, "the legacy format does not state what the node is"


def test_legacy_link_without_a_key() -> None:
    link = parse_mega_url(f"https://mega.nz/#!{HANDLE}")

    assert link is not None
    assert link.kind is MegaLinkKind.FILE
    assert link.has_key is False


@pytest.mark.parametrize(
    "host",
    ["mega.nz", "www.mega.nz", "mega.co.nz", "www.mega.co.nz", "MEGA.NZ"],
)
def test_supported_hosts(host: str) -> None:
    assert parse_mega_url(f"https://{host}/file/{HANDLE}#{FILE_KEY}") is not None


def test_plain_http_is_accepted() -> None:
    assert parse_mega_url(f"http://mega.nz/file/{HANDLE}#{FILE_KEY}") is not None


def test_keys_are_case_sensitive() -> None:
    lower = parse_mega_url(f"https://mega.nz/file/{HANDLE}#{FILE_KEY}")
    upper = parse_mega_url(f"https://mega.nz/file/{HANDLE}#{FILE_KEY.upper()}")

    assert lower is not None
    assert upper is not None
    assert lower.key != upper.key


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://example.test/file/AaBbCcDd#key", "wrong host"),
        ("https://notmega.nz/file/AaBbCcDd", "host that merely looks similar"),
        ("https://mega.nz.example.test/file/AaBbCcDd", "mega as a subdomain of another host"),
        ("https://mega.nz/pro", "a Mega page that is not a share"),
        ("https://mega.nz/", "the bare site"),
        ("https://mega.nz/file/AaBbCc", "handle shorter than eight characters"),
        ("https://mega.nz/file/waytoolonghandle", "handle longer than eight characters"),
        ("https://mega.nz/file/Aa!BbCcD", "handle outside the base64url alphabet"),
        ("https://mega.nz/document/AaBbCcDd", "unknown share type"),
        ("https://mega.nz/#!", "legacy marker without a handle"),
        ("https://mega.nz/#F!", "legacy folder marker without a handle"),
        ("https://mega.nz/#!short!key", "legacy handle of the wrong length"),
        ("https://mega.nz/#AaBbCcDd", "legacy fragment without the bang marker"),
        ("https://mega.nz/help#!AaBbCcDd!key", "legacy fragment on a non-root path"),
        ("ftp://mega.nz/file/AaBbCcDd", "unsupported scheme"),
        ("/file/AaBbCcDd", "relative URL"),
        ("", "empty string"),
    ],
)
def test_rejects_invalid_and_malformed_urls(url: str, reason: str) -> None:
    assert parse_mega_url(url) is None, reason


def test_surrounding_whitespace_is_tolerated() -> None:
    assert parse_mega_url(f"  https://mega.nz/file/{HANDLE}#{FILE_KEY}  ") is not None


def test_trailing_slash_is_tolerated() -> None:
    assert parse_mega_url(f"https://mega.nz/file/{HANDLE}/#{FILE_KEY}") is not None


def test_links_differing_only_in_handle_parse_differently() -> None:
    first = parse_mega_url(f"https://mega.nz/#!{HANDLE}!{FILE_KEY}")
    second = parse_mega_url(f"https://mega.nz/#!ZzYyXxWw!{FILE_KEY}")

    assert first is not None
    assert second is not None
    assert first != second
    assert first.handle != second.handle


def test_identical_links_parse_equal() -> None:
    url = f"https://mega.nz/file/{HANDLE}#{FILE_KEY}"

    assert parse_mega_url(url) == parse_mega_url(url)
