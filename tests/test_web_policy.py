"""Tests for the crawl policy seam.

The seam matters more than the one implementation behind it today, so these
tests assert the shape a future ``RobotsPolicy`` will have to satisfy.
"""

from urllib.parse import urlsplit

from maxicrawler.web.policy import AllowAllPolicy, CrawlPolicy, PolicyDecision


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
