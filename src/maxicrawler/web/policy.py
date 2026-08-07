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

``ScopePolicy``
    Same host only, a path prefix, a maximum depth. All of them are the same
    question asked about a different property of the URL.

``PrivateNetworkPolicy``
    Refuses loopback, link-local, and private address space. Not needed while
    the operator types the URL; required before any web interface accepts one
    from someone else.

``CompositePolicy``
    Asks several in turn; the first refusal wins.

A refusal is a **value**, not an exception, so a recursive crawl can record
*"skipped: disallowed by robots.txt"* against a URL and carry on. The service
turns a refusal of the URL it was explicitly asked for into
:class:`~maxicrawler.web.errors.PolicyRefusedError`, because there refusing is
a failure of the request; a crawl loop catches that per URL rather than letting
it end the run.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Whether a URL may be fetched, and why not when it may not."""

    allowed: bool
    reason: str | None = None
    """A short phrase naming the rule, for a report or a log line."""

    @classmethod
    def allow(cls) -> "PolicyDecision":
        """Return the decision to permit a fetch."""
        return cls(allowed=True)

    @classmethod
    def refuse(cls, reason: str) -> "PolicyDecision":
        """Return the decision to refuse a fetch, naming the rule."""
        return cls(allowed=False, reason=reason)

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

    The default, and the honest one for this sprint: MaxiCrawler fetches the
    single page its operator named, which is what a browser does when the same
    person types the same address.
    """

    def may_fetch(self, url: str) -> PolicyDecision:
        """Return the decision to permit *url*."""
        return PolicyDecision.allow()
