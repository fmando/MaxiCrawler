"""Tests for the shared address rule, and for what makes sharing it possible.

The long table of spellings for "this machine" is exercised through the crawl
policy in ``tests/test_web_private.py`` and is not repeated here. What this file
covers is the part that is new: the rule answers in a vocabulary neither caller
owns, and it sits somewhere both callers may reach.
"""

import ast
from pathlib import Path

import pytest

from maxicrawler.utils.addresses import (
    PrivateNetworkRule,
    is_internal,
    is_metadata,
    names_a_private_zone,
    parse_address,
)
from maxicrawler.web.private import PrivateNetworkPolicy

MODULE = Path("src/maxicrawler/utils/addresses.py")


def make_rule(
    answers: dict[str, tuple[str, ...]] | None = None, **kwargs: object
) -> PrivateNetworkRule:
    """Return a rule resolving names from *answers* rather than from DNS."""
    resolved = answers or {}
    return PrivateNetworkRule(
        resolver=lambda host: resolved.get(host, ()),
        **kwargs,  # type: ignore[arg-type]
    )


# --- the shared vocabulary ---------------------------------------------------


def test_the_rule_answers_with_a_sentence_rather_than_an_exception() -> None:
    """Both callers report a refusal in their own words.

    A rule that raised would have picked one of those vocabularies for
    everybody, and the wrong one for somebody: a crawl records a skipped page,
    a transfer fails.
    """
    reason = make_rule().refusal_for("http://127.0.0.1/secrets")

    assert isinstance(reason, str)
    assert "not a public address" in reason


def test_a_permitted_url_gets_no_sentence_at_all() -> None:
    assert (
        make_rule({"example.test": ("93.184.216.34",)}).refusal_for("https://example.test/") is None
    )


def test_a_url_with_no_host_is_not_the_rule_s_business() -> None:
    """Refusing here would report the wrong reason for a URL nothing can fetch."""
    assert make_rule().refusal_for("mailto:someone@example.test") is None


def test_a_metadata_service_stays_refused_when_private_networks_are_allowed() -> None:
    """Opening an intranet is not the same decision as handing over a credential."""
    rule = make_rule(allow_private=True)

    assert rule.refusal_for("http://127.0.0.1/") is None
    assert "cloud metadata service" in (rule.refusal_for("http://169.254.169.254/") or "")


@pytest.mark.parametrize(
    ("host", "internal"),
    [
        ("10.0.0.1", True),
        ("127.0.0.1", True),
        ("::1", True),
        ("::ffff:127.0.0.1", True),
        ("93.184.216.34", False),
    ],
)
def test_the_pure_helpers_are_usable_without_the_rule_around_them(
    host: str, internal: bool
) -> None:
    """A caller may want the facts and its own arrangement of them."""
    address = parse_address(host)

    assert address is not None
    assert is_internal(address) is internal


def test_a_metadata_address_is_recognized_on_its_own() -> None:
    address = parse_address("169.254.169.254")

    assert address is not None
    assert is_metadata(address) is True


@pytest.mark.parametrize(
    ("host", "private"),
    [
        ("wiki.internal", True),
        ("localhost", True),
        ("printer.local", True),
        ("example.org", False),
    ],
)
def test_a_name_can_mean_inside_without_a_lookup(host: str, private: bool) -> None:
    assert names_a_private_zone(host) is private


# --- one judgement, two vocabularies -----------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/admin",
    ],
)
def test_the_crawl_policy_says_exactly_what_the_rule_said(url: str) -> None:
    """The invariant that keeps a second implementation from creeping back.

    The policy is an adapter. The day its reason stops being the rule's
    sentence is the day two answers exist, and this is what notices.
    """
    rule = make_rule()
    policy = PrivateNetworkPolicy(resolver=lambda host: ())

    decision = policy.may_fetch(url)

    assert decision.allowed is False
    assert decision.reason == rule.refusal_for(url)


def test_the_policy_speaks_for_a_rule_it_will_name() -> None:
    policy = PrivateNetworkPolicy(allow_private=True)

    assert isinstance(policy.rule, PrivateNetworkRule)


# --- where it lives ----------------------------------------------------------


def test_the_rule_imports_neither_package_that_depends_on_it() -> None:
    """The whole reason it moved.

    `web` and `providers` must both be able to reach this, and neither may
    reach the other. An import added here in either direction would make the
    module unusable by one of its two callers -- and would do so at the moment
    somebody tried, rather than here.
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for name in _names_of(node)
    }

    assert not [name for name in imported if name.startswith("maxicrawler.web")]
    assert not [name for name in imported if name.startswith("maxicrawler.providers")]


def test_the_rule_depends_on_nothing_of_ours_at_all() -> None:
    """Stronger, and true today: it is standard library only.

    Stated as its own test because the one above would still pass if this grew
    a dependency on, say, `config` -- which would drag settings into a module
    two packages import for one fact.
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    ours = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for name in _names_of(node)
        if name.startswith("maxicrawler")
    }

    assert ours == set()


def _names_of(node: ast.Import | ast.ImportFrom) -> list[str]:
    """Return the module names *node* imports."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    return [node.module] if node.module else []
