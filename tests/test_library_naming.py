"""Tests for the rules that turn untrusted names into path components."""

import pytest
from doubles import make_ref

from maxicrawler.library import (
    FALLBACK_FILENAME,
    MAX_FILENAME_LENGTH,
    LibraryLayoutError,
    provider_directory,
    resource_key,
    safe_filename,
)


def test_a_resource_key_keeps_a_readable_stem() -> None:
    key = resource_key(make_ref("AaBbCcDd"))

    assert key.startswith("aabbccdd-")


def test_a_resource_key_is_stable_for_the_same_reference() -> None:
    assert resource_key(make_ref()) == resource_key(make_ref())


def test_a_resource_key_ignores_the_credential() -> None:
    with_key = resource_key(make_ref(secret="0123456789abcdefghijkl"))
    without_key = resource_key(make_ref())

    assert with_key == without_key


def test_a_resource_key_separates_identifiers_that_differ_only_in_case() -> None:
    upper = resource_key(make_ref("AABBCCDD"))
    lower = resource_key(make_ref("aabbccdd"))

    assert upper != lower
    assert upper.casefold() != lower.casefold()


def test_a_resource_key_separates_providers() -> None:
    assert resource_key(make_ref(provider="mega")) != resource_key(make_ref(provider="gofile"))


def test_a_resource_key_separates_containers() -> None:
    loose = resource_key(make_ref("FileAAA1"))
    contained = resource_key(make_ref("FileAAA1", parent_id="FolderAA"))

    assert loose != contained


def test_a_resource_key_cannot_be_confused_by_a_separator_in_an_identifier() -> None:
    joined = resource_key(make_ref("A", parent_id="B"))
    swapped = resource_key(make_ref("B", parent_id="A"))

    assert joined != swapped


def test_a_resource_key_survives_an_identifier_without_usable_characters() -> None:
    key = resource_key(make_ref("///"))

    assert key
    assert "/" not in key


@pytest.mark.parametrize("name", ["mega", "Mega", "me-ga"])
def test_a_provider_directory_is_reduced_to_a_safe_alphabet(name: str) -> None:
    assert provider_directory(name) == "mega"


def test_a_provider_directory_rejects_an_unusable_name() -> None:
    with pytest.raises(LibraryLayoutError, match="no usable directory"):
        provider_directory("///")


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/passwd",
        "..\\..\\Windows\\System32\\config",
        "/etc/passwd",
        "C:\\Windows\\notepad.exe",
        "nested/path/report.txt",
    ],
)
def test_a_filename_can_never_escape_its_directory(name: str) -> None:
    result = safe_filename(name)

    assert "/" not in result
    assert "\\" not in result
    assert result not in {".", ".."}


def test_a_filename_keeps_an_ordinary_name_verbatim() -> None:
    assert safe_filename("ubuntu-24.04.iso") == "ubuntu-24.04.iso"


def test_a_filename_keeps_non_ascii_characters() -> None:
    assert safe_filename("Anleitung — Kapitel 1.pdf") == "Anleitung — Kapitel 1.pdf"


@pytest.mark.parametrize("name", [None, "", "   ", ".", "..", "/", "///"])
def test_an_unusable_filename_falls_back(name: str | None) -> None:
    assert safe_filename(name) == FALLBACK_FILENAME


def test_a_filename_replaces_characters_windows_forbids() -> None:
    result = safe_filename('re:port|"1".txt')

    assert not set(result) & set('<>:"|?*')


def test_a_filename_drops_a_trailing_dot_windows_would_swallow() -> None:
    assert safe_filename("report.") == "report"


def test_a_filename_avoids_a_reserved_device_name() -> None:
    assert safe_filename("CON.txt") != "CON.txt"
    assert safe_filename("nul") != "nul"


def test_a_long_filename_is_shortened_but_keeps_its_extension() -> None:
    result = safe_filename("x" * 400 + ".iso")

    assert len(result) <= MAX_FILENAME_LENGTH
    assert result.endswith(".iso")


def test_a_control_character_never_reaches_a_filename() -> None:
    result = safe_filename("re\x00port\n.txt")

    assert "\x00" not in result
    assert "\n" not in result
