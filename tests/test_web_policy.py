"""Tests for the crawl policy seam.

The seam matters more than the one implementation behind it today, so these
tests assert the shape a future ``RobotsPolicy`` will have to satisfy.
"""

from urllib.parse import urlsplit

import pytest

from maxicrawler.web.policy import (
    AllowAllPolicy,
    CompositePolicy,
    CrawlPolicy,
    PathPrefixPolicy,
    PolicyDecision,
    PolicyRule,
    SameDomainPolicy,
    path_prefix,
    registrable_host,
)


class SameHostPolicy:
    """A stand-in for the scope rules a recursive crawl will need."""

    def __init__(self, host: str) -> None:
        self._host = host

    def may_fetch(self, url: str) -> PolicyDecision:
        """Permit *url* only when it lives on the configured host."""
        if urlsplit(url).hostname == self._host:
            return PolicyDecision.allow()
        return PolicyDecision.refuse("outside the crawl scope")


def test_the_default_policy_satisfies_the_runtime_protocol() -> None:
    assert isinstance(AllowAllPolicy(), CrawlPolicy)


def test_a_third_party_policy_satisfies_the_protocol_structurally() -> None:
    assert isinstance(SameHostPolicy("example.test"), CrawlPolicy)


def test_the_default_policy_permits_everything() -> None:
    decision = AllowAllPolicy().may_fetch("https://example.test/anything")

    assert decision.allowed is True
    assert decision.reason is None


def test_a_decision_is_truthy_when_it_permits() -> None:
    assert bool(PolicyDecision.allow()) is True


def test_a_decision_is_falsy_when_it_refuses() -> None:
    assert bool(PolicyDecision.refuse("nope")) is False


def test_a_refusal_carries_its_reason() -> None:
    decision = PolicyDecision.refuse("disallowed by robots.txt")

    assert decision.allowed is False
    assert decision.reason == "disallowed by robots.txt"


def test_a_refusal_names_the_kind_of_rule_that_refused() -> None:
    decision = PolicyDecision.refuse("disallowed by robots.txt", rule=PolicyRule.ROBOTS)

    assert decision.rule is PolicyRule.ROBOTS


def test_a_refusal_that_classifies_itself_counts_as_scope() -> None:
    """Every refusal is at minimum "not one of mine", so that is the default."""
    assert PolicyDecision.refuse("nope").rule is PolicyRule.SCOPE


def test_a_refusal_is_a_value_rather_than_an_exception() -> None:
    """A recursive crawl has to record a refusal and keep going."""
    policy = SameHostPolicy("example.test")

    decisions = [
        policy.may_fetch("https://example.test/a"),
        policy.may_fetch("https://elsewhere.test/b"),
        policy.may_fetch("https://example.test/c"),
    ]

    assert [bool(decision) for decision in decisions] == [True, False, True]


def test_a_decision_is_immutable() -> None:
    decision = PolicyDecision.allow()

    assert decision == PolicyDecision(allowed=True, reason=None)


# --- the same-domain policy --------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.org/a", "example.org"),
        ("https://www.example.org/a", "example.org"),
        ("https://WWW.Example.ORG/a", "example.org"),
        ("https://docs.example.org/a", "docs.example.org"),
        ("https://wwwexample.org/a", "wwwexample.org"),
        ("https://example.org:8443/a", "example.org"),
        ("mailto:someone@example.org", None),
    ],
)
def test_the_host_is_reduced_to_the_form_two_spellings_share(
    url: str, expected: str | None
) -> None:
    assert registrable_host(url) == expected


def test_the_same_domain_policy_satisfies_the_protocol() -> None:
    assert isinstance(SameDomainPolicy("https://example.org/"), CrawlPolicy)


def test_a_url_on_the_seed_host_is_permitted() -> None:
    policy = SameDomainPolicy("https://example.org/start")

    assert policy.may_fetch("https://example.org/other").allowed is True


def test_www_and_the_bare_host_are_one_site() -> None:
    policy = SameDomainPolicy("https://www.example.org/start")

    assert policy.may_fetch("https://example.org/other").allowed is True
    assert policy.may_fetch("https://www.example.org/other").allowed is True


def test_the_scheme_does_not_change_the_site() -> None:
    policy = SameDomainPolicy("https://example.org/")

    assert policy.may_fetch("http://example.org/other").allowed is True


def test_another_host_is_refused_with_a_reason() -> None:
    policy = SameDomainPolicy("https://example.org/")

    decision = policy.may_fetch("https://mega.nz/file/AaBbCcDd")

    assert decision.allowed is False
    assert decision.reason == "outside example.org"
    assert decision.rule is PolicyRule.SCOPE


def test_a_subdomain_is_outside_the_scope_by_default() -> None:
    policy = SameDomainPolicy("https://example.org/")

    assert policy.may_fetch("https://docs.example.org/a").allowed is False


def test_a_subdomain_can_be_included() -> None:
    policy = SameDomainPolicy("https://example.org/", include_subdomains=True)

    assert policy.may_fetch("https://docs.example.org/a").allowed is True
    assert policy.may_fetch("https://deep.docs.example.org/a").allowed is True


def test_a_lookalike_host_is_not_inside_the_scope() -> None:
    """The classic hole in a same-domain rule.

    `evilexample.org` ends with `example.org`, so a suffix test would hand a
    crawl to somebody else's site. The check is label-wise instead.
    """
    policy = SameDomainPolicy("https://example.org/", include_subdomains=True)

    assert policy.may_fetch("https://evilexample.org/a").allowed is False
    assert policy.may_fetch("https://example.org.evil.test/a").allowed is False


def test_a_parent_domain_is_outside_a_subdomain_scope() -> None:
    policy = SameDomainPolicy("https://docs.example.org/", include_subdomains=True)

    assert policy.may_fetch("https://example.org/a").allowed is False


def test_a_url_without_a_host_is_refused() -> None:
    policy = SameDomainPolicy("https://example.org/")

    assert policy.may_fetch("mailto:someone@example.org").allowed is False


def test_a_seed_without_a_host_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="cannot read a host"):
        SameDomainPolicy("not a url")


def test_the_policy_names_the_host_it_guards() -> None:
    assert SameDomainPolicy("https://www.example.org/").host == "example.org"


# --- the path prefix ---------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.org/hr/", "/hr/"),
        ("https://example.org/hr", "/hr/"),
        ("https://example.org/", "/"),
        ("https://example.org", "/"),
        ("https://example.org/a/b/c/", "/a/b/c/"),
        ("https://example.org/hr/?page=2", "/hr/"),
        ("https://example.org/hr/#top", "/hr/"),
        # A last segment with a dot in it is read as a file, so the place is
        # the directory holding it.
        ("https://example.org/docs/guide.html", "/docs/"),
        ("https://example.org/index.html", "/"),
        # ...and a trailing slash overrules the guess.
        ("https://example.org/releases/v1.0/", "/releases/v1.0/"),
    ],
)
def test_the_prefix_is_the_place_the_url_names(url: str, expected: str) -> None:
    assert path_prefix(url) == expected


def test_a_dotted_last_segment_is_read_as_a_file_even_when_it_is_not() -> None:
    """The one guess in the module, asserted so it is a decision, not a bug.

    `/releases/v1.0` is a directory to whoever wrote it and a file to this
    rule, which widens the scope to `/releases/`. Written down here because a
    rule that is wrong in a known case and silent about it is worse than one
    that is wrong and says so.
    """
    assert path_prefix("https://example.org/releases/v1.0") == "/releases/"


def test_the_path_prefix_policy_satisfies_the_protocol() -> None:
    assert isinstance(PathPrefixPolicy("https://example.org/hr/"), CrawlPolicy)


def test_the_seed_place_itself_is_inside() -> None:
    policy = PathPrefixPolicy("https://example.org/hr/")

    assert policy.may_fetch("https://example.org/hr/").allowed is True


def test_the_place_is_the_same_written_with_or_without_a_trailing_slash() -> None:
    """`/hr` and `/hr/` are one place, whichever of them a link was written as."""
    for seed in ("https://example.org/hr", "https://example.org/hr/"):
        policy = PathPrefixPolicy(seed)

        assert policy.may_fetch("https://example.org/hr").allowed is True
        assert policy.may_fetch("https://example.org/hr/").allowed is True


def test_a_page_below_the_seed_is_inside() -> None:
    policy = PathPrefixPolicy("https://example.org/hr/")

    assert policy.may_fetch("https://example.org/hr/thread/12345").allowed is True


def test_a_sibling_section_on_the_same_host_is_refused() -> None:
    """The whole point: one domain, and only one of its sections wanted."""
    policy = PathPrefixPolicy("https://example.org/hr/")

    decision = policy.may_fetch("https://example.org/g/")

    assert decision.allowed is False
    assert decision.reason == "outside example.org/hr/"
    assert decision.rule is PolicyRule.SCOPE


def test_the_parent_of_the_seed_is_outside() -> None:
    policy = PathPrefixPolicy("https://example.org/hr/")

    assert policy.may_fetch("https://example.org/").allowed is False


def test_a_lookalike_path_is_not_inside_the_scope() -> None:
    """The same hole a suffix test opens in a same-domain rule.

    `/hrx/` starts with `/hr` as a string and is a different section. Matching
    is by whole segment for exactly this reason.
    """
    policy = PathPrefixPolicy("https://example.org/hr/")

    assert policy.may_fetch("https://example.org/hrx/").allowed is False
    assert policy.may_fetch("https://example.org/hr-archive").allowed is False


def test_a_query_string_does_not_move_a_url_out_of_the_place() -> None:
    policy = PathPrefixPolicy("https://example.org/hr/")

    assert policy.may_fetch("https://example.org/hr/?page=2").allowed is True


def test_another_host_is_outside_however_its_path_reads() -> None:
    """A path without a host would admit any site that happens to have `/hr/`."""
    policy = PathPrefixPolicy("https://example.org/hr/")

    assert policy.may_fetch("https://elsewhere.test/hr/").allowed is False


def test_a_subdomain_is_outside_a_path_scope() -> None:
    """Not an option here: a place below `example.org/hr/` is on that host."""
    policy = PathPrefixPolicy("https://example.org/hr/")

    assert policy.may_fetch("https://docs.example.org/hr/").allowed is False


def test_www_and_the_bare_host_are_one_site_here_too() -> None:
    policy = PathPrefixPolicy("https://www.example.org/hr/")

    assert policy.may_fetch("https://example.org/hr/thread/1").allowed is True


def test_the_scheme_does_not_move_a_url_out_of_the_place() -> None:
    policy = PathPrefixPolicy("https://example.org/hr/")

    assert policy.may_fetch("http://example.org/hr/thread/1").allowed is True


def test_a_seed_naming_a_document_covers_its_directory() -> None:
    policy = PathPrefixPolicy("https://example.org/docs/guide.html")

    assert policy.may_fetch("https://example.org/docs/guide.html").allowed is True
    assert policy.may_fetch("https://example.org/docs/api.html").allowed is True
    assert policy.may_fetch("https://example.org/blog/").allowed is False


def test_a_seed_at_the_root_admits_the_whole_host() -> None:
    policy = PathPrefixPolicy("https://example.org/")

    assert policy.may_fetch("https://example.org/anything/at/all").allowed is True
    assert policy.may_fetch("https://elsewhere.test/").allowed is False


def test_a_url_without_a_host_is_refused_by_the_path_policy() -> None:
    policy = PathPrefixPolicy("https://example.org/hr/")

    assert policy.may_fetch("mailto:someone@example.org").allowed is False


def test_a_seed_without_a_host_is_refused_at_construction_here_too() -> None:
    with pytest.raises(ValueError, match="cannot read a host"):
        PathPrefixPolicy("not a url")


def test_the_path_policy_names_the_place_it_guards() -> None:
    policy = PathPrefixPolicy("https://www.example.org/hr")

    assert policy.host == "example.org"
    assert policy.prefix == "/hr/"


# --- the composite -----------------------------------------------------------


def test_an_empty_composite_permits_everything() -> None:
    assert CompositePolicy().may_fetch("https://anywhere.test/").allowed is True


def test_a_composite_permits_what_every_policy_permits() -> None:
    composite = CompositePolicy([AllowAllPolicy(), SameDomainPolicy("https://example.org/")])

    assert composite.may_fetch("https://example.org/a").allowed is True


def test_the_first_refusal_wins_and_keeps_its_reason() -> None:
    composite = CompositePolicy(
        [SameDomainPolicy("https://example.org/"), SameHostPolicy("elsewhere.test")]
    )

    decision = composite.may_fetch("https://elsewhere.test/a")

    assert decision.allowed is False
    assert decision.reason == "outside example.org"


def test_a_composite_satisfies_the_protocol_it_composes() -> None:
    assert isinstance(CompositePolicy([AllowAllPolicy()]), CrawlPolicy)


def test_a_composite_reports_the_policies_it_asks() -> None:
    inner = AllowAllPolicy()

    assert CompositePolicy([inner]).policies == (inner,)
