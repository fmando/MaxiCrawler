"""The invariant that a share key never leaves this process.

A Mega link carries its decryption key in the URL fragment, which no HTTP
client transmits. MaxiCrawler must preserve that property rather than merely
inherit it, so the checks here treat it as a testable invariant instead of a
convention: nothing that is sent, rendered, or logged may contain the key.
"""

import ast
import logging
from pathlib import Path

import pytest
from doubles import make_record
from mega_fixtures import (
    CHILD_FILE_HANDLE,
    FILE_AES_KEY,
    SHARE_KEY,
    RecordingTransport,
    encode_base64,
    file_answer,
    file_url,
    folder_answer,
    folder_url,
    pack_file_key,
)

from maxicrawler.domain import ResourceInspection, UrlCategory, UrlClassification
from maxicrawler.providers import CryptographyCipherBackend, Retrier, RetryPolicy
from maxicrawler.providers.mega import MegaApiClient, MegaProvider

FILE_KEY = encode_base64(pack_file_key(FILE_AES_KEY))
FOLDER_KEY = encode_base64(SHARE_KEY)
WINDOW = 8
"""Length of the substrings scanned for; shorter runs collide by chance."""

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "maxicrawler"

REVEALING_MODULES = {"providers/mega/provider.py"}
"""The only modules allowed to unwrap a :class:`ResourceSecret`.

This set is deliberately exact. Widening it is a security decision and should
require editing this test on purpose.
"""


def run(url: str, answers: list[object]) -> tuple[ResourceInspection, RecordingTransport]:
    """Inspect *url* against queued answers and return the result and traffic."""
    transport = RecordingTransport(answers)
    retrier = Retrier(RetryPolicy(max_attempts=1), sleep=lambda _: None)
    mega = MegaProvider(
        MegaApiClient(transport, retrier=retrier), cipher=CryptographyCipherBackend()
    )
    classification = UrlClassification(
        record=make_record(url), category=UrlCategory.FILE, plugin_name="mega"
    )
    return mega.inspect(mega.reference(classification)), transport


def assert_absent(key: str, haystack: str, what: str) -> None:
    """Assert that no eight-character run of *key* occurs in *haystack*."""
    assert key not in haystack, f"the whole key reached {what}"
    for start in range(len(key) - WINDOW + 1):
        window = key[start : start + WINDOW]
        assert window not in haystack, f"a fragment of the key reached {what}: {start}"


def test_the_file_key_never_reaches_the_transport() -> None:
    _, transport = run(file_url(key=FILE_AES_KEY), [[file_answer()]])

    assert transport.calls
    assert_absent(FILE_KEY, transport.everything_sent(), "an outgoing request")


def test_the_folder_key_never_reaches_the_transport() -> None:
    _, transport = run(folder_url(), [[folder_answer()]])

    assert transport.calls
    assert_absent(FOLDER_KEY, transport.everything_sent(), "an outgoing request")


def test_the_folder_key_never_reaches_the_transport_for_a_contained_entry() -> None:
    url = f"{folder_url()}/file/{CHILD_FILE_HANDLE}"

    _, transport = run(url, [[folder_answer()]])

    assert_absent(FOLDER_KEY, transport.everything_sent(), "an outgoing request")


def test_the_key_never_appears_in_a_rendered_inspection() -> None:
    inspection, _ = run(folder_url(), [[folder_answer()]])

    assert inspection.entries
    assert_absent(FOLDER_KEY, repr(inspection), "a repr")
    assert_absent(FOLDER_KEY, str(inspection), "a str")


def test_the_key_never_appears_in_a_rendered_reference() -> None:
    inspection, _ = run(file_url(key=FILE_AES_KEY), [[file_answer()]])

    assert inspection.ref.has_secret is True
    assert_absent(FILE_KEY, repr(inspection.ref), "a repr")
    assert_absent(FILE_KEY, inspection.ref.url, "a reference URL")


def test_the_key_never_appears_in_a_rendered_entry() -> None:
    inspection, _ = run(folder_url(), [[folder_answer()]])

    for entry in inspection.entries:
        assert entry.ref.has_secret is True
        assert_absent(FOLDER_KEY, repr(entry), "an entry repr")


def test_the_key_never_reaches_a_log_record(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        run(folder_url(), [[folder_answer()]])

    assert_absent(FOLDER_KEY, caplog.text, "a log record")


def test_the_key_never_appears_in_a_failure() -> None:
    inspection, _ = run(file_url(key=FILE_AES_KEY), [[-9]])

    assert inspection.metadata is None
    assert_absent(FILE_KEY, repr(inspection), "a failed inspection")


def unwraps_a_secret(path: Path) -> bool:
    """Return whether the module at *path* calls :meth:`ResourceSecret.reveal`.

    The check reads the syntax tree rather than the text, so prose about the
    method in a docstring does not count as a use of it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(isinstance(node, ast.Attribute) and node.attr == "reveal" for node in ast.walk(tree))


def imported_names(path: Path) -> set[str]:
    """Return every name the module at *path* imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names.update(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def test_only_the_provider_unwraps_a_secret() -> None:
    revealing = {
        path.relative_to(SOURCE_ROOT).as_posix()
        for path in SOURCE_ROOT.rglob("*.py")
        if unwraps_a_secret(path)
    }

    assert revealing == REVEALING_MODULES


def test_the_wire_layer_knows_nothing_about_secrets() -> None:
    for module in ("providers/mega/api.py", "providers/transport.py"):
        path = SOURCE_ROOT / module
        assert "ResourceSecret" not in imported_names(path)
        assert not unwraps_a_secret(path)


def test_the_crypto_layer_knows_nothing_about_the_network() -> None:
    for module in ("providers/crypto.py", "providers/mega/crypto.py", "providers/mega/download.py"):
        imported = imported_names(SOURCE_ROOT / module)
        assert not {name for name in imported if name.startswith(("urllib", "http", "socket"))}
        assert "maxicrawler.providers.transport" not in imported
