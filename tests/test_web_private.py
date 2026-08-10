"""Tests for the private-network guard.

The table is the test. What matters about a rule like this is not that it works
on one example but that a long list of spellings for "this machine" all end up
refused -- which is exactly where guards of this kind fail in the wild.
"""

import pytest

from maxicrawler.web import CrawlPolicy, PolicyRefusedError, PolicyRule
from maxicrawler.web.private import PrivateNetworkPolicy, redirect_guard


def make_policy(
    answers: dict[str, tuple[str, ...]] | None = None, **kwargs: object
) -> PrivateNetworkPolicy:
    """Return a policy resolving names from *answers* rather than from DNS."""
    resolved = answers or {}
    return PrivateNetworkPolicy(
        resolver=lambda host: resolved.get(host, ()),
        **kwargs,  # type: ignore[arg-type]
    )


def refuses(policy: PrivateNetworkPolicy, url: str) -> bool:
    """Return whether *policy* refuses *url*."""
    return not policy.may_fetch(url).allowed


# --- addresses written into the URL ------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/a",
        "http://127.0.0.1:8000/a",
        "http://127.1/a",
        "http://127.255.255.254/a",
        "http://0x7f.0.0.1/a",
        "http://[::1]/a",
        "http://[::ffff:127.0.0.1]/a",
        "http://10.0.0.1/a",
        "http://10.255.255.255/a",
        "http://172.16.0.1/a",
        "http://172.31.255.255/a",
        "http://192.168.1.1/a",
        "http://169.254.1.1/a",
        "http://[fe80::1]/a",
        "http://[fc00::1]/a",
        "http://[fd12:3456::1]/a",
        "http://100.64.0.1/a",
        "http://0.0.0.0/a",
        "http://[::]/a",
    ],
)
def test_an_address_that_is_not_the_public_internet_is_refused(url: str) -> None:
    assert refuses(make_policy(), url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/a",
        "http://8.8.8.8/a",
        "http://172.32.0.1/a",
        "http://11.0.0.1/a",
        "http://[2606:4700::1111]/a",
    ],
)
def test_a_public_address_is_permitted(url: str) -> None:
    assert refuses(make_policy(), url) is False


def test_an_ipv4_address_wearing_an_ipv6_hat_is_still_loopback() -> None:
    """The spelling a guard that only looks at the outer form lets through."""
    assert refuses(make_policy(), "http://[::ffff:127.0.0.1]/a") is True


@pytest.mark.parametrize("host", ["127.1", "0x7f.0.0.1", "2130706433", "0177.0.0.1"])
def test_the_shorthand_spellings_of_loopback_are_refused(host: str) -> None:
    """The classic way past a guard like this.

    `ipaddress` is strict and calls these host names; the C resolver every
    socket goes through accepts all of them and reaches 127.0.0.1. A check that
    believed only the strict reading would resolve nothing and permit the
    fetch, and the connection would go to loopback anyway.
    """
    assert refuses(make_policy(), f"http://{host}/a") is True


def test_a_refusal_names_the_private_network_rule() -> None:
    decision = make_policy().may_fetch("http://10.0.0.1/a")

    assert decision.rule is PolicyRule.PRIVATE_NETWORK
    assert decision.reason is not None


# --- names --------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/a",
        "http://localhost:8000/a",
        "http://LOCALHOST/a",
        "http://api.localhost/a",
        "http://printer.local/a",
        "http://wiki.internal/a",
        "http://nas.home.arpa/a",
        "http://localhost./a",
    ],
)
def test_a_name_that_means_this_network_is_refused_without_a_lookup(url: str) -> None:
    """A resolver that answers nothing proves no lookup was needed."""
    assert refuses(make_policy(), url) is True


def test_a_name_is_judged_by_what_it_resolves_to() -> None:
    """The case a literal check cannot see: an ordinary name, an inside address."""
    policy = make_policy({"intranet.example.org": ("10.1.2.3",)})

    assert refuses(policy, "https://intranet.example.org/a") is True


def test_a_name_resolving_to_the_public_internet_is_permitted() -> None:
    policy = make_policy({"example.org": ("93.184.216.34",)})

    assert refuses(policy, "https://example.org/a") is False


def test_every_answer_is_judged_rather_than_the_first() -> None:
    """One public answer must not cover for a private one."""
    policy = make_policy({"mixed.test": ("93.184.216.34", "127.0.0.1")})

    assert refuses(policy, "https://mixed.test/a") is True


def test_a_name_that_does_not_resolve_is_not_evidence_of_anything() -> None:
    """The fetch will fail on its own and say so properly."""
    assert refuses(make_policy(), "https://nowhere.invalid/a") is False


def test_a_host_is_resolved_once() -> None:
    asked: list[str] = []

    def resolver(host: str) -> tuple[str, ...]:
        asked.append(host)
        return ("93.184.216.34",)

    policy = PrivateNetworkPolicy(resolver=resolver)
    for path in ("/a", "/b", "/c"):
        policy.may_fetch(f"https://example.org{path}")

    assert asked == ["example.org"]


def test_a_url_without_a_host_is_not_this_policys_business() -> None:
    assert refuses(make_policy(), "mailto:someone@example.org") is False


# --- the metadata service -----------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.170.2/v2/credentials",
        "http://100.100.100.200/latest/meta-data/",
        "http://[fd00:ec2::254]/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
    ],
)
def test_a_cloud_metadata_service_is_refused(url: str) -> None:
    assert refuses(make_policy(), url) is True


def test_a_name_resolving_to_the_metadata_service_is_refused() -> None:
    policy = make_policy({"harmless.test": ("169.254.169.254",)})

    assert refuses(policy, "https://harmless.test/a") is True


def test_allowing_private_addresses_does_not_allow_the_metadata_service() -> None:
    """Opening an intranet is not volunteering a cloud credential.

    The two are one setting only by accident of both being "not the public
    internet", and an operator who crawls their own network has not said
    anything about their instance's credentials.
    """
    policy = make_policy(allow_private=True)

    assert refuses(policy, "http://192.168.1.10/a") is False
    assert refuses(policy, "http://169.254.169.254/latest/meta-data/") is True


def test_the_reason_says_what_was_refused() -> None:
    decision = make_policy().may_fetch("http://169.254.169.254/a")

    assert decision.reason is not None
    assert "metadata" in decision.reason


# --- the escapes --------------------------------------------------------------


def test_private_addresses_can_be_allowed_wholesale() -> None:
    policy = make_policy(allow_private=True)

    assert refuses(policy, "http://192.168.1.10/a") is False
    assert refuses(policy, "http://127.0.0.1:8000/a") is False


def test_allowing_loopback_allows_it_under_both_of_its_names() -> None:
    """`localhost` is loopback written out.

    Refusing the name while permitting the address would make one machine
    reachable under one spelling and not the other, which is a rule nobody
    could predict.
    """
    policy = make_policy(allow_private=True)

    assert refuses(policy, "http://localhost:8000/a") is False
    assert refuses(policy, "http://wiki.internal/a") is False
    assert refuses(policy, "http://metadata.google.internal/a") is True


def test_one_address_can_be_allowed_without_opening_the_rest() -> None:
    """The homelab case: crawl my own wiki, nothing else inside."""
    policy = make_policy(allow=["192.168.1.20"])

    assert refuses(policy, "http://192.168.1.20/wiki") is False
    assert refuses(policy, "http://192.168.1.21/wiki") is True


def test_a_block_can_be_allowed() -> None:
    policy = make_policy(allow=["10.0.0.0/8"])

    assert refuses(policy, "http://10.1.2.3/a") is False
    assert refuses(policy, "http://192.168.1.1/a") is True


def test_a_name_can_be_allowed() -> None:
    policy = make_policy(allow=["localhost"])

    assert refuses(policy, "http://localhost:8000/a") is False
    assert refuses(policy, "http://127.0.0.1:8000/a") is True


def test_an_allowed_name_covers_what_it_resolves_to() -> None:
    policy = make_policy({"wiki.test": ("10.1.2.3",)}, allow=["wiki.test"])

    assert refuses(policy, "https://wiki.test/a") is False


def test_an_ipv6_block_does_not_cover_an_ipv4_address() -> None:
    policy = make_policy(allow=["fd00::/8"])

    assert refuses(policy, "http://10.0.0.1/a") is True


def test_an_empty_allowance_is_ignored() -> None:
    assert refuses(make_policy(allow=["", "  "]), "http://10.0.0.1/a") is True


# --- the literal-only form ----------------------------------------------------


def test_the_literal_form_asks_no_resolver() -> None:
    """What the engine's first gate uses: free, and enough for a written-out address."""

    def resolver(host: str) -> tuple[str, ...]:
        message = "the literal check resolved a name"
        raise AssertionError(message)

    policy = PrivateNetworkPolicy(resolve=False, resolver=resolver)

    assert refuses(policy, "http://127.0.0.1/a") is True
    assert refuses(policy, "http://localhost/a") is True
    assert refuses(policy, "https://intranet.example.org/a") is False


# --- the redirect guard -------------------------------------------------------


def test_the_guard_lets_a_public_target_through() -> None:
    guard = redirect_guard(make_policy({"example.org": ("93.184.216.34",)}))

    assert guard("https://example.org/a") is None


def test_the_guard_refuses_a_target_inside() -> None:
    """Where SSRF actually lives: the destination, not the URL somebody typed."""
    guard = redirect_guard(make_policy())

    with pytest.raises(PolicyRefusedError) as refusal:
        guard("http://169.254.169.254/latest/meta-data/")

    assert refusal.value.rule is PolicyRule.PRIVATE_NETWORK
    assert "redirected to" in str(refusal.value)


def test_the_policy_satisfies_the_crawl_policy_protocol() -> None:
    assert isinstance(make_policy(), CrawlPolicy)
