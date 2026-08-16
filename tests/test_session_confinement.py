"""The invariant that a borrowed session goes to the wire and nowhere else.

A Mega share key must never leave the process at all; a session is different in
kind, because it is only useful once it is on the wire. The invariant is
therefore not *"it is never sent"* but *"it is sent to one host and appears
nowhere a human or a disk would keep it"* — not in a repr, not in a log, not in
a configuration file, not in a database row, and not in the message of the
exception raised when it turns out to be wrong.

Those last two matter most in practice. A session in ``to_toml`` output would
be copied into every configuration backup, and a session in an error string
would be copied into every bug report.
"""

import ast
import logging
from pathlib import Path

import pytest

from maxicrawler.web.cookies import CookieError, CookieJar

SECRET = "mu_sid=s3cr3t-session-value"
DOMAIN = "musescore.com"
SCORE_URL = "https://musescore.com/user/21965011/scores/4217351"
SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "maxicrawler"

CARRYING_MODULES = {"web/cookies.py"}
"""The only modules allowed to hold a :class:`CookieJar` type at all.

Deliberately exact, and deliberately separate from the reveal check: a module
that merely *names* the type is one refactor away from storing one, and the
whole design depends on the session travelling from settings to transport
without stopping anywhere that renders itself. Widening this set is the moment
to ask where the new holder writes things down.

Modules that legitimately pass a jar through — the provider wiring, the
settings loader — are added here as they arrive, each with a reason.
"""


def jar() -> CookieJar:
    """Return a jar holding a recognisable value."""
    return CookieJar.from_header_line(SECRET, domain=DOMAIN)


def test_the_session_reaches_the_host_it_belongs_to() -> None:
    """The invariant is confinement, not secrecy; a session that never goes out is useless."""
    assert jar().header_for(SCORE_URL) == SECRET


def test_the_session_never_appears_in_a_rendering() -> None:
    held = jar()

    assert SECRET not in repr(held)
    assert SECRET not in str(held)
    assert "s3cr3t" not in repr(held)
    assert "s3cr3t" not in str(held)


def test_the_names_are_visible_so_a_status_page_needs_no_values() -> None:
    """A page should be able to say the session is here without putting it on screen."""
    held = CookieJar.from_header_line(f"{SECRET}; cf_clearance=abc", domain=DOMAIN)

    assert held.names == ("mu_sid", "cf_clearance")
    assert all("s3cr3t" not in name for name in held.names)


def test_the_session_never_appears_in_a_failure(tmp_path: Path) -> None:
    """A bug report carries the exception text, so the exception carries no session.

    The failure worth testing is the one a person actually hits: an export from
    a browser that was logged into something else, or a domain typed wrong. The
    file holds a session, the jar refuses to be built, and the complaint names
    the file rather than quoting it.
    """
    path = tmp_path / "cookies.txt"
    path.write_text(
        "\t".join((".other.example", "TRUE", "/", "TRUE", "1900000000", "mu_sid", "s3cr3t-value")),
        encoding="utf-8",
    )

    with pytest.raises(CookieError) as raised:
        CookieJar.from_file(path, domain=DOMAIN)

    assert "cookies.txt" in str(raised.value)
    assert "s3cr3t" not in str(raised.value)


def test_the_session_never_reaches_a_log_record(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        held = jar()
        held.header_for(SCORE_URL)
        logging.getLogger(__name__).debug("jar in hand: %r", held)

    assert "s3cr3t" not in caplog.text


def names_in(path: Path) -> set[str]:
    """Return every identifier the module at *path* mentions in its syntax tree.

    Reading the tree rather than the text means prose about cookies in a
    docstring does not count as holding one.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        if isinstance(node, ast.Attribute):
            found.add(node.attr)
        if isinstance(node, ast.alias):
            found.add(node.name.rsplit(".", 1)[-1])
    return found


def test_only_the_declared_modules_hold_a_jar() -> None:
    carrying = {
        path.relative_to(SOURCE_ROOT).as_posix()
        for path in SOURCE_ROOT.rglob("*.py")
        if "CookieJar" in names_in(path)
    }

    assert carrying == CARRYING_MODULES


def test_the_persistence_layer_knows_nothing_about_sessions() -> None:
    """A session in a database row outlives every reason to have kept it."""
    for path in (SOURCE_ROOT / "database").rglob("*.py"):
        mentioned = names_in(path)
        assert "CookieJar" not in mentioned
        assert "reveal" not in mentioned


def test_the_configuration_writer_never_writes_a_session() -> None:
    """``to_toml`` round-trips every field it knows, so it must not know this one."""
    settings = SOURCE_ROOT / "config" / "settings.py"
    mentioned = names_in(settings)

    assert "CookieJar" not in mentioned
    assert "reveal" not in mentioned
