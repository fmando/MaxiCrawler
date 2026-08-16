"""Reading a session somebody exported, and refusing to hand it to the wrong host.

The interesting cases here are all failure modes that would be invisible from
the outside: a jar that parsed cleanly and dropped the cookies that mattered, a
jar that answered a plaintext redirect, a jar that mistook a lookalike domain
for the real one. Each of those produces either a session that quietly does not
work or one that quietly goes somewhere it should not, so each gets a test.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from maxicrawler.web.cookies import CookieError, CookieJar

DOMAIN = "musescore.com"
SCORE_URL = "https://musescore.com/user/21965011/scores/4217351"
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
LONG_AFTER = "1900000000"
LONG_BEFORE = "1500000000"


def netscape(*rows: tuple[str, str, str, str]) -> str:
    """Return a cookies.txt document holding *rows* of host, expiry, name, value."""
    header = "# Netscape HTTP Cookie File\n"
    lines = [
        "\t".join((host, "TRUE", "/", "TRUE", expiry, name, value))
        for host, expiry, name, value in rows
    ]
    return header + "\n".join(lines) + "\n"


def jar_from(*rows: tuple[str, str, str, str]) -> CookieJar:
    """Return a jar built from *rows* against a fixed clock."""
    return CookieJar.from_netscape(netscape(*rows), domain=DOMAIN, clock=lambda: NOW)


def test_a_header_line_becomes_a_session() -> None:
    jar = CookieJar.from_header_line("mu_sid=abc; cf_clearance=xyz", domain=DOMAIN)

    assert jar.names == ("mu_sid", "cf_clearance")
    assert jar.header_for(SCORE_URL) == "mu_sid=abc; cf_clearance=xyz"


def test_the_word_cookie_in_front_of_the_line_is_tolerated() -> None:
    """Told to copy the line, a person copies the line.

    Edge's header view shows ``cookie: name=value``, and refusing that would be
    refusing the exact thing the instructions ask for.
    """
    jar = CookieJar.from_header_line("cookie: mu_sid=abc", domain=DOMAIN)

    assert jar.header_for(SCORE_URL) == "mu_sid=abc"


def test_a_netscape_export_becomes_the_same_session() -> None:
    jar = jar_from((".musescore.com", LONG_AFTER, "mu_sid", "abc"))

    assert jar.names == ("mu_sid",)
    assert jar.header_for(SCORE_URL) == "mu_sid=abc"


def test_the_shape_is_recognised_without_being_announced() -> None:
    """One tab-bearing line is the whole difference, and nobody has to say which."""
    from_file = CookieJar.from_text(
        netscape((".musescore.com", LONG_AFTER, "mu_sid", "abc")), domain=DOMAIN, clock=lambda: NOW
    )
    from_line = CookieJar.from_text("mu_sid=abc", domain=DOMAIN)

    assert from_file.header_for(SCORE_URL) == from_line.header_for(SCORE_URL)


def test_an_httponly_cookie_survives_the_export() -> None:
    """The ``#HttpOnly_`` prefix looks like a comment and carries the session.

    Dropping it would leave a jar that parses, holds the harmless cookies, and
    fails every request — the hardest failure to diagnose from the outside.
    """
    document = "#HttpOnly_" + "\t".join(
        (".musescore.com", "TRUE", "/", "TRUE", LONG_AFTER, "mu_sid", "abc")
    )

    jar = CookieJar.from_netscape(document, domain=DOMAIN, clock=lambda: NOW)

    assert jar.names == ("mu_sid",)


def test_cookies_for_other_hosts_are_dropped_rather_than_refused() -> None:
    """A browser export is the whole browser; the other nine hosts are normal."""
    jar = jar_from(
        (".example.org", LONG_AFTER, "other", "1"),
        (".musescore.com", LONG_AFTER, "mu_sid", "abc"),
    )

    assert jar.names == ("mu_sid",)


def test_an_expired_cookie_is_dropped() -> None:
    jar = jar_from(
        (".musescore.com", LONG_BEFORE, "stale", "1"),
        (".musescore.com", LONG_AFTER, "mu_sid", "abc"),
    )

    assert jar.names == ("mu_sid",)


def test_a_session_cookie_has_no_expiry_and_is_kept() -> None:
    """Expiry ``0`` means it dies with the browser, which is the one worth carrying."""
    jar = jar_from((".musescore.com", "0", "mu_sid", "abc"))

    assert jar.names == ("mu_sid",)


def test_a_subdomain_is_the_same_account() -> None:
    jar = CookieJar.from_header_line("mu_sid=abc", domain=DOMAIN)

    assert jar.header_for("https://ja.musescore.com/user/1/scores/2") is not None


def test_a_lookalike_domain_gets_nothing() -> None:
    """``musescore.com.evil.example`` ends with the domain and is not the domain."""
    jar = CookieJar.from_header_line("mu_sid=abc", domain=DOMAIN)

    assert jar.header_for("https://musescore.com.evil.example/") is None
    assert jar.header_for("https://evil.example/") is None


def test_plaintext_gets_nothing() -> None:
    """A redirect to http is the cheapest way to read a session off the wire."""
    jar = CookieJar.from_header_line("mu_sid=abc", domain=DOMAIN)

    assert jar.header_for("http://musescore.com/user/1/scores/2") is None


def test_loopback_is_exempt_so_the_real_path_can_be_tested() -> None:
    jar = CookieJar.from_header_line("mu_sid=abc", domain="127.0.0.1")

    assert jar.header_for("http://127.0.0.1:8000/score") == "mu_sid=abc"


def test_a_jar_that_would_hold_nothing_is_refused() -> None:
    """Said at the point it can still be fixed, not at the first request."""
    with pytest.raises(CookieError, match="no cookies"):
        CookieJar.from_header_line("", domain=DOMAIN)

    with pytest.raises(CookieError, match="no cookies"):
        jar_from((".example.org", LONG_AFTER, "other", "1"))


def test_a_jar_without_a_host_is_refused() -> None:
    with pytest.raises(CookieError, match="host"):
        CookieJar.from_header_line("mu_sid=abc", domain="  ")


def test_a_missing_file_says_which_file(tmp_path: Path) -> None:
    missing = tmp_path / "nowhere.txt"

    with pytest.raises(CookieError, match="nowhere.txt"):
        CookieJar.from_file(missing, domain=DOMAIN)


def test_a_file_that_holds_nothing_says_which_file(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    path.write_text("# nothing here\n", encoding="utf-8")

    with pytest.raises(CookieError, match="cookies.txt"):
        CookieJar.from_file(path, domain=DOMAIN)


def test_a_file_is_read_in_either_shape(tmp_path: Path) -> None:
    path = tmp_path / "session.txt"
    path.write_text("cookie: mu_sid=abc\n", encoding="utf-8")

    jar = CookieJar.from_file(path, domain=DOMAIN)

    assert jar.header_for(SCORE_URL) == "mu_sid=abc"
