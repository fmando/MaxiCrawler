"""Deciding whether a URL may be fetched at all.

This module is deliberately almost empty. It exists now, before anything needs
it, because it is the seam every politeness and scope rule will plug into, and
a seam introduced later is a redesign of everything that grew without it.

What plugs in, and why none of it changes this file:

``RobotsPolicy``
    Fetches ``/robots.txt`` through the same
    :class:`~maxicrawler.web.fetcher.PageFetcher` the crawl already uses — no
    second I/O seam is needed — parses it with the standard library's
    :mod:`urllib.robotparser`, and caches the answer per host.

``PrivateNetworkPolicy``
    Refuses loopback, link-local, and private address space. Not needed while
    the operator types the URL; required before any web interface accepts one
    from someone else.

Rate limiting is *not* on this list. "May I fetch this?" and "may I fetch it
*yet*?" are different questions, and the second belongs to a
:class:`~maxicrawler.web.fetcher.PageFetcher` decorator, where waiting does not
block a policy check.

Depth is not on this list either: it is a property of the frontier item rather
than of the URL, so the engine holds that limit.

A refusal is a **value**, not an exception, so a recursive crawl can record
*"skipped: disallowed by robots.txt"* against a URL and carry on. The service
turns a refusal of the URL it was explicitly asked for into
:class:`~maxicrawler.web.errors.PolicyRefusedError`, because there refusing is
a failure of the request; a crawl loop catches that per URL rather than letting
it end the run.

A refusal carries two descriptions of itself: ``reason`` is a phrase for a
person, and :class:`PolicyRule` is the same fact for a program. A report groups
its skips by the second, so *"outside my scope"* and *"forbidden by
robots.txt"* stay two different answers however either of them is worded.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit


class PolicyRule(StrEnum):
    """Which kind of rule turned a URL away.

    Deliberately coarse. This is not a list of policies — anybody may write one
    — but a list of the answers a *reader* of a report can act on, and there
    are three: it is not mine to crawl, its owner said no, or it points inside
    this network.
    """

    SCOPE = "scope"
    """Not a URL this crawl covers. The bucket for any policy that names none.

    Every refusal is at minimum this, which is why it is the default: a policy
    that says no without classifying itself has still said *"not one of mine"*.
    """

    ROBOTS = "robots"
    """Forbidden by the ``/robots.txt`` of the host that would answer."""

    PRIVATE_NETWORK = "private network"
    """Points at this machine, this network, or a cloud metadata service."""


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Whether a URL may be fetched, and why not when it may not."""

    allowed: bool
    reason: str | None = None
    """A short phrase naming the rule, for a report or a log line."""

    rule: PolicyRule = PolicyRule.SCOPE
    """Which kind of rule refused, for a counter rather than a reader.

    Meaningless on a decision that permits, and ignored there. Refusals are
    what anything counts.
    """

    @classmethod
    def allow(cls) -> "PolicyDecision":
        """Return the decision to permit a fetch."""
        return cls(allowed=True)

    @classmethod
    def refuse(cls, reason: str, *, rule: PolicyRule = PolicyRule.SCOPE) -> "PolicyDecision":
        """Return the decision to refuse a fetch, naming the rule."""
        return cls(allowed=False, reason=reason, rule=rule)

    def __bool__(self) -> bool:
        """Return whether the fetch is permitted."""
        return self.allowed


@runtime_checkable
class CrawlPolicy(Protocol):
    """Answers whether one URL may be fetched.

    Implementations must be safe to call repeatedly and in any order. They may
    perform I/O — reading ``/robots.txt`` is I/O — but should cache it, because
    a recursive crawl will ask about every URL it finds.
    """

    def may_fetch(self, url: str) -> PolicyDecision:
        """Return whether *url* may be retrieved."""
        ...


class AllowAllPolicy:
    """Permits every URL.

    The default, and the honest one for now: MaxiCrawler fetches what its
    operator named, which is what a browser does when the same person types the
    same address.
    """

    def may_fetch(self, url: str) -> PolicyDecision:
        """Return the decision to permit *url*."""
        return PolicyDecision.allow()


def registrable_host(url: str) -> str | None:
    """Return the host of *url* reduced to the form two spellings share.

    Lowercased, with a leading ``www.`` removed, so ``www.example.org`` and
    ``example.org`` are one site — which is what a reader means by "the same
    domain" and what every site that serves both spellings assumes.

    This is deliberately **not** a registrable domain in the Public Suffix List
    sense. Computing that needs the list itself, which is a dependency and a
    file that goes stale; without it, ``example.co.uk`` and ``other.co.uk``
    cannot be told apart from two subdomains of one site. The limitation is
    stated rather than hidden, and it only ever makes the scope *narrower* than
    a reader might expect, never wider.
    """
    host = urlsplit(url).hostname
    if host is None:
        return None
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


class SameDomainPolicy:
    """Keeps a crawl on the host it started from.

    Off unless asked for. MaxiCrawler serves two workflows equally: crawling
    one website, where staying on it is the point, and hunting for share links,
    where the interesting URLs are on *other* hosts by definition.

    Subdomains are outside the scope unless *include_subdomains* says
    otherwise, and the check is label-wise rather than a suffix test —
    ``evilexample.org`` ends with ``example.org`` but is a different site
    owned by somebody else. Getting that wrong is the classic hole in a
    same-domain rule, so it is asserted by name in the tests.
    """

    def __init__(self, seed_url: str, *, include_subdomains: bool = False) -> None:
        host = registrable_host(seed_url)
        if host is None:
            msg = f"cannot read a host from the seed URL: {seed_url}"
            raise ValueError(msg)
        self._host = host
        self._include_subdomains = include_subdomains

    @property
    def host(self) -> str:
        """Return the host a crawl is confined to."""
        return self._host

    def may_fetch(self, url: str) -> PolicyDecision:
        """Return whether *url* is on the seed's host."""
        host = registrable_host(url)
        if host is None:
            return PolicyDecision.refuse("no host")
        if host == self._host:
            return PolicyDecision.allow()
        if self._include_subdomains and host.endswith(f".{self._host}"):
            return PolicyDecision.allow()
        return PolicyDecision.refuse(f"outside {self._host}")


class CompositePolicy:
    """Asks several policies in turn; the first refusal wins.

    How scope, robots.txt and a private-network guard will be combined without
    any of them knowing about the others. An empty composite permits
    everything, which makes it a safe default rather than a trap.
    """

    def __init__(self, policies: Iterable[CrawlPolicy] = ()) -> None:
        self._policies = tuple(policies)

    @property
    def policies(self) -> tuple[CrawlPolicy, ...]:
        """Return the policies consulted, in order."""
        return self._policies

    def may_fetch(self, url: str) -> PolicyDecision:
        """Return the first refusal, or permission when none refuses."""
        for policy in self._policies:
            decision = policy.may_fetch(url)
            if not decision.allowed:
                return decision
        return PolicyDecision.allow()
