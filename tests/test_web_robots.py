"""Tests for reading a robots.txt document.

These assert the behaviour MaxiCrawler depends on rather than Protego's whole
surface. The point of a test over a library is to state what would have to stay
true if it were ever replaced -- which is exactly the list in ADR-029.
"""

import pytest

from maxicrawler.web import (
    ContentEncodingError,
    ContentTypeError,
    CrawlPolicy,
    FetchedPage,
    FetchError,
    HttpStatusError,
    PolicyRule,
    ResponseTooLargeError,
    TooManyRedirectsError,
    TransportError,
)
from maxicrawler.web.robots import (
    MAX_ROBOTS_BYTES,
    RobotsPolicy,
    RobotsRules,
    decode_robots,
    origin_of,
    product_token,
    robots_url,
)

TOKEN = "MaxiCrawler"


def rules(document: str) -> RobotsRules:
    """Return the rules stated by *document*."""
    return RobotsRules.parse(document)


# --- the product token -------------------------------------------------------


@pytest.mark.parametrize(
    ("user_agent", "expected"),
    [
        ("MaxiCrawler/0.1.0", "MaxiCrawler"),
        ("MaxiCrawler", "MaxiCrawler"),
        ("MaxiCrawler/0.1.0 (+https://example.org/bot)", "MaxiCrawler"),
        ("MaxiCrawler (+https://example.org/bot)", "MaxiCrawler"),
        ("  spaced/1.0  ", "spaced"),
        ("", "*"),
        ("   ", "*"),
        ("/1.0", "*"),
    ],
)
def test_the_token_is_the_product_without_its_version(user_agent: str, expected: str) -> None:
    assert product_token(user_agent) == expected


def test_a_group_written_for_the_product_covers_the_versioned_agent() -> None:
    """Why the token exists at all."""
    document = "User-agent: MaxiCrawler\nDisallow: /private/\n"

    assert (
        rules(document).allows("https://e.org/private/x", token=product_token("MaxiCrawler/0.1.0"))
        is False
    )


# --- the origin --------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.org/a/b?c=d#e", "https://example.org"),
        ("https://Example.ORG/a", "https://example.org"),
        ("https://example.org:443/a", "https://example.org"),
        ("http://example.org:80/a", "http://example.org"),
        ("https://example.org:8443/a", "https://example.org:8443"),
        ("http://example.org/", "http://example.org"),
    ],
)
def test_the_origin_is_scheme_host_and_a_non_default_port(url: str, expected: str) -> None:
    assert origin_of(url) == expected


def test_http_and_https_are_two_origins() -> None:
    """robots.txt is published per authority, and the scheme is part of one."""
    assert origin_of("http://example.org/a") != origin_of("https://example.org/a")


def test_a_url_without_a_host_has_no_origin() -> None:
    with pytest.raises(ValueError, match="cannot read a host"):
        origin_of("mailto:someone@example.org")


def test_the_rules_are_looked_for_at_the_root_of_the_origin() -> None:
    assert robots_url("https://example.org:8443/deep/page?x=1") == (
        "https://example.org:8443/robots.txt"
    )


# --- decoding ----------------------------------------------------------------


def test_a_byte_order_mark_does_not_swallow_the_first_group() -> None:
    """The one thing Protego does not do for us.

    Without this the first line reads as a directive nobody recognises, the
    group is lost, and a document that forbids everything permits everything.
    """
    document = "﻿User-agent: *\nDisallow: /\n".encode()

    assert RobotsRules.parse(document).allows("https://e.org/a", token=TOKEN) is False


def test_undecodable_bytes_cost_their_line_rather_than_the_file() -> None:
    document = b"User-agent: *\nDisallow: /caf\xe9\nDisallow: /admin\n"

    assert RobotsRules.parse(document).allows("https://e.org/admin", token=TOKEN) is False


def test_decoding_is_utf_8_whatever_a_server_announced() -> None:
    assert decode_robots("Disallow: /café".encode()) == "Disallow: /café"


def test_the_read_limit_is_the_one_the_rfc_asks_for() -> None:
    assert MAX_ROBOTS_BYTES >= 500 * 1024


# --- matching ----------------------------------------------------------------


def test_a_missing_file_restricts_nothing() -> None:
    assert RobotsRules.unrestricted().allows("https://e.org/anything", token=TOKEN) is True


def test_an_unreachable_file_forbids_everything() -> None:
    assert RobotsRules.forbidding().allows("https://e.org/", token=TOKEN) is False


def test_an_empty_document_restricts_nothing() -> None:
    assert rules("").allows("https://e.org/a", token=TOKEN) is True


def test_a_disallowed_prefix_is_refused() -> None:
    document = "User-agent: *\nDisallow: /private/\n"

    assert rules(document).allows("https://e.org/private/x", token=TOKEN) is False
    assert rules(document).allows("https://e.org/public/x", token=TOKEN) is True


def test_an_empty_disallow_restricts_nothing() -> None:
    assert rules("User-agent: *\nDisallow:\n").allows("https://e.org/a", token=TOKEN) is True


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://e.org/a/b/c.pdf", False),
        ("https://e.org/c.pdf", False),
        ("https://e.org/c.pdf?download=1", True),
        ("https://e.org/c.html", True),
    ],
)
def test_a_wildcard_and_an_end_anchor_are_honoured(url: str, allowed: bool) -> None:
    """What `urllib.robotparser` gets wrong, and the reason for the dependency."""
    assert rules("User-agent: *\nDisallow: /*.pdf$\n").allows(url, token=TOKEN) is allowed


def test_a_wildcard_in_the_middle_of_a_path_is_honoured() -> None:
    document = "User-agent: *\nDisallow: /admin/*/edit\n"

    assert rules(document).allows("https://e.org/admin/7/edit", token=TOKEN) is False
    assert rules(document).allows("https://e.org/admin/7/view", token=TOKEN) is True


def test_the_longest_matching_rule_wins() -> None:
    document = "User-agent: *\nDisallow: /a/\nAllow: /a/b/\n"

    assert rules(document).allows("https://e.org/a/b/c", token=TOKEN) is True
    assert rules(document).allows("https://e.org/a/x", token=TOKEN) is False


def test_allow_breaks_a_tie() -> None:
    assert (
        rules("User-agent: *\nAllow: /p\nDisallow: /p\n").allows("https://e.org/p", token=TOKEN)
        is True
    )


def test_the_order_rules_are_written_in_does_not_matter() -> None:
    first = "User-agent: *\nDisallow: /a/\nAllow: /a/b/\n"
    second = "User-agent: *\nAllow: /a/b/\nDisallow: /a/\n"

    assert rules(first).allows("https://e.org/a/b/c", token=TOKEN) == rules(second).allows(
        "https://e.org/a/b/c", token=TOKEN
    )


# --- groups ------------------------------------------------------------------


GROUPED = (
    "User-agent: *\n"
    "Disallow: /\n"
    "\n"
    "User-agent: MaxiCrawler\n"
    "Disallow: /private/\n"
    "\n"
    "User-agent: Googlebot\n"
    "Disallow: /nope/\n"
)


def test_a_group_naming_this_crawler_replaces_the_catch_all() -> None:
    assert rules(GROUPED).allows("https://e.org/open", token=TOKEN) is True


def test_the_named_group_still_applies() -> None:
    assert rules(GROUPED).allows("https://e.org/private/x", token=TOKEN) is False


def test_another_crawlers_group_is_not_ours() -> None:
    assert rules(GROUPED).allows("https://e.org/nope/x", token=TOKEN) is True


def test_the_catch_all_covers_a_crawler_with_no_group_of_its_own() -> None:
    assert rules(GROUPED).allows("https://e.org/open", token="SomebodyElse") is False


def test_the_token_is_matched_without_regard_to_case() -> None:
    assert rules(GROUPED).allows("https://e.org/private/x", token="maxicrawler") is False


def test_two_agent_lines_share_one_group() -> None:
    document = "User-agent: A\nUser-agent: MaxiCrawler\nDisallow: /x/\n"

    assert rules(document).allows("https://e.org/x/y", token=TOKEN) is False


def test_a_document_naming_nobody_we_are_restricts_nothing() -> None:
    assert (
        rules("User-agent: Googlebot\nDisallow: /\n").allows("https://e.org/a", token=TOKEN) is True
    )


# --- crawl-delay -------------------------------------------------------------


def test_a_delay_is_read_from_the_group_that_applies() -> None:
    document = "User-agent: *\nCrawl-delay: 10\n\nUser-agent: MaxiCrawler\nCrawl-delay: 2.5\n"

    assert rules(document).crawl_delay(TOKEN) == 2.5


def test_a_delay_falls_back_to_the_catch_all_group() -> None:
    document = "User-agent: *\nCrawl-delay: 10\n\nUser-agent: MaxiCrawler\nDisallow: /x\n"

    assert rules(document).crawl_delay("SomebodyElse") == 10.0


def test_a_file_that_asks_for_no_delay_says_so() -> None:
    assert rules("User-agent: *\nDisallow: /x\n").crawl_delay(TOKEN) is None


def test_a_delay_is_reported_as_it_was_written() -> None:
    """Clamping is a decision about us, not a fact about the file."""
    assert rules("User-agent: *\nCrawl-delay: 86400\n").crawl_delay(TOKEN) == 86400.0


# --- shape -------------------------------------------------------------------


def test_comments_and_windows_line_endings_are_tolerated() -> None:
    document = "# a comment\r\nUser-agent: *\r\nDisallow: /a  # why\r\n"

    assert rules(document).allows("https://e.org/a", token=TOKEN) is False


def test_reading_the_rules_opens_no_socket(no_sockets: None) -> None:
    """Parsing is pure; fetching belongs to the policy above this."""
    assert rules("User-agent: *\nDisallow: /\n").allows("https://e.org/a", token=TOKEN) is False


@pytest.fixture
def no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any attempt to open a socket fail loudly."""

    def forbidden(*args: object, **kwargs: object) -> object:
        message = "robots parsing opened a socket"
        raise AssertionError(message)

    monkeypatch.setattr("socket.socket", forbidden)


# --- the policy over the rules -----------------------------------------------


class FakeFetcher:
    """A `PageFetcher` that answers from a script rather than from a network."""

    def __init__(self, body: bytes | FetchError = b"", *, content_type: str = "text/plain") -> None:
        self._body = body
        self._content_type = content_type
        self.asked: list[str] = []

    def fetch(self, url: str) -> FetchedPage:
        self.asked.append(url)
        if isinstance(self._body, FetchError):
            raise self._body
        return FetchedPage(
            requested_url=url,
            final_url=url,
            status=200,
            body=self._body,
            content_type=self._content_type,
        )


def make_policy(body: bytes | FetchError = b"", **kwargs: object) -> RobotsPolicy:
    """Return a policy over a fetcher answering with *body*."""
    return RobotsPolicy(FakeFetcher(body), user_agent="MaxiCrawler/0.1.0", **kwargs)  # type: ignore[arg-type]


def test_the_policy_satisfies_the_crawl_policy_protocol() -> None:
    assert isinstance(make_policy(), CrawlPolicy)


def test_a_permitted_url_is_permitted() -> None:
    policy = make_policy(b"User-agent: *\nDisallow: /private/\n")

    assert policy.may_fetch("https://e.org/public").allowed is True


def test_a_disallowed_url_is_refused_under_the_robots_rule() -> None:
    policy = make_policy(b"User-agent: *\nDisallow: /private/\n")

    decision = policy.may_fetch("https://e.org/private/x")

    assert decision.allowed is False
    assert decision.rule is PolicyRule.ROBOTS
    assert decision.reason is not None
    assert "robots.txt" in decision.reason


def test_the_rules_are_looked_for_once_per_origin() -> None:
    fetcher = FakeFetcher(b"User-agent: *\nDisallow: /private/\n")
    policy = RobotsPolicy(fetcher, user_agent="MaxiCrawler/0.1.0")

    for path in ("/a", "/b", "/private/c", "/d"):
        policy.may_fetch(f"https://e.org{path}")

    assert fetcher.asked == ["https://e.org/robots.txt"]


def test_two_origins_are_asked_separately() -> None:
    fetcher = FakeFetcher(b"")
    policy = RobotsPolicy(fetcher, user_agent="MaxiCrawler/0.1.0")

    policy.may_fetch("https://one.test/a")
    policy.may_fetch("https://two.test/a")
    policy.may_fetch("https://one.test/b")

    assert fetcher.asked == ["https://one.test/robots.txt", "https://two.test/robots.txt"]


def test_the_rules_are_asked_for_at_the_root_however_deep_the_url() -> None:
    fetcher = FakeFetcher(b"")
    RobotsPolicy(fetcher, user_agent="MaxiCrawler/0.1.0").may_fetch("https://e.org/a/b/c?d=e#f")

    assert fetcher.asked == ["https://e.org/robots.txt"]


def test_a_url_without_a_host_is_not_this_policys_business() -> None:
    """Refusing it here would report the wrong reason for it."""
    assert make_policy().may_fetch("mailto:someone@example.org").allowed is True


# --- what a failure to read the rules means ----------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 429])
def test_a_client_error_leaves_the_crawler_free(status: int) -> None:
    """RFC 9309: "unavailable" means a crawler may access any resource."""
    policy = make_policy(HttpStatusError(f"HTTP {status}", status=status))

    assert policy.may_fetch("https://e.org/anything").allowed is True


@pytest.mark.parametrize("status", [500, 502, 503])
def test_a_server_error_forbids_everything(status: int) -> None:
    """RFC 9309: "unreachable" means assume complete disallow.

    We do not know what this host allows, and helping ourselves to the benefit
    of the doubt is exactly what a crawler must not do.
    """
    policy = make_policy(HttpStatusError(f"HTTP {status}", status=status))

    assert policy.may_fetch("https://e.org/anything").allowed is False


def test_a_host_that_cannot_be_reached_forbids_everything() -> None:
    policy = make_policy(TransportError("connection refused"))

    assert policy.may_fetch("https://e.org/anything").allowed is False


def test_denying_on_error_can_be_turned_off() -> None:
    policy = make_policy(TransportError("connection refused"), deny_on_error=False)

    assert policy.may_fetch("https://e.org/anything").allowed is True


@pytest.mark.parametrize(
    "failure",
    [
        ContentTypeError("a page, not rules", content_type="text/html"),
        ResponseTooLargeError("more than we will hold"),
        ContentEncodingError("a broken body"),
        TooManyRedirectsError("a chain that never resolved"),
    ],
)
def test_rules_we_declined_to_read_are_not_a_host_saying_no(failure: FetchError) -> None:
    """Unreadable is not the same as unreachable.

    A soft 404 served as HTML, a document larger than we will hold, a redirect
    loop -- none of them is a server failing, so each reads as "this host
    published no robots.txt".
    """
    assert make_policy(failure).may_fetch("https://e.org/anything").allowed is True


def test_a_failure_is_remembered_rather_than_repeated() -> None:
    """Otherwise every URL on a dead host pays for the same failed request."""
    fetcher = FakeFetcher(TransportError("connection refused"))
    policy = RobotsPolicy(fetcher, user_agent="MaxiCrawler/0.1.0")

    for path in ("/a", "/b", "/c"):
        assert policy.may_fetch(f"https://e.org{path}").allowed is False

    assert len(fetcher.asked) == 1


# --- the delay ---------------------------------------------------------------


def test_the_delay_comes_from_the_group_that_applies() -> None:
    policy = make_policy(b"User-agent: MaxiCrawler\nCrawl-delay: 2.5\n")

    assert policy.delay_for("https://e.org/a") == 2.5


def test_a_host_that_asks_for_no_delay_gets_none() -> None:
    assert make_policy(b"User-agent: *\nDisallow: /x\n").delay_for("https://e.org/a") is None


def test_a_hostile_delay_is_clamped() -> None:
    """One line in a stranger's file must not be able to freeze a crawl."""
    policy = make_policy(b"User-agent: *\nCrawl-delay: 86400\n", max_delay=30.0)

    assert policy.delay_for("https://e.org/a") == 30.0


def test_a_delay_under_the_clamp_is_kept() -> None:
    policy = make_policy(b"User-agent: *\nCrawl-delay: 5\n", max_delay=30.0)

    assert policy.delay_for("https://e.org/a") == 5.0


def test_the_delay_is_answered_from_the_same_cache_as_the_rules() -> None:
    fetcher = FakeFetcher(b"User-agent: *\nCrawl-delay: 1\n")
    policy = RobotsPolicy(fetcher, user_agent="MaxiCrawler/0.1.0")

    policy.may_fetch("https://e.org/a")
    policy.delay_for("https://e.org/a")

    assert len(fetcher.asked) == 1


def test_the_policy_is_matched_under_its_product_token() -> None:
    policy = make_policy(b"User-agent: MaxiCrawler\nDisallow: /private/\n")

    assert policy.token == "MaxiCrawler"
    assert policy.may_fetch("https://e.org/private/x").allowed is False


def test_a_negative_clamp_is_refused() -> None:
    with pytest.raises(ValueError, match="max_delay"):
        make_policy(max_delay=-1.0)
