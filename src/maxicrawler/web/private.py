"""Keeping a crawl off this machine, this network, and the metadata service.

Until Sprint 11 the only person who could name a URL was the person running
the program, and a program that fetches what its operator typed is doing its
job. A web interface changes that: a URL now arrives from a browser, and a
browser can be pointed at a form by any page it visits. ``http://localhost:…``
and ``http://169.254.169.254/…`` stop being odd and start being an attack.

**This module is the crawl's half of the answer, not the whole of it.** What
counts as an internal address, and why one is refused, lives in
:mod:`maxicrawler.utils.addresses` — because a provider opening a transfer has
to reach the same verdict about the same URL, and cannot import this package.
The rule there answers with a sentence; :class:`PrivateNetworkPolicy` turns
that sentence into a :class:`~maxicrawler.web.policy.CrawlPolicy` decision, so
neither the engine nor the fetcher learns what an internal address is. They ask
and are told, exactly as they already are about scope and robots.txt.

The two checks — the pure one that reads the URL and the costly one that asks
the resolver — and what neither of them closes are described where they are
implemented. What belongs here is how a crawl *uses* them:

*   the engine's first gate holds a policy built with ``resolve=False``, so a
    page full of links to this machine never reaches the frontier and no
    lookup is paid for a URL that was never going to be next;
*   the gate immediately before each request holds a resolving one;
*   and the fetcher calls :func:`redirect_guard` on **every hop**, because a
    public URL answering ``302 Location: http://169.254.169.254/`` would walk
    straight past a check made once at the start. The adapter exists so that
    :mod:`maxicrawler.web.fetcher` never imports a policy: it is handed a
    function that raises, which is all it needs to know.
"""

from collections.abc import Callable, Iterable

from maxicrawler.utils.addresses import PrivateNetworkRule, Resolver
from maxicrawler.web.errors import PolicyRefusedError
from maxicrawler.web.policy import PolicyDecision, PolicyRule


class PrivateNetworkPolicy:
    """Refuses URLs that point inside, rather than out.

    A :class:`~maxicrawler.web.policy.CrawlPolicy` over
    :class:`~maxicrawler.utils.addresses.PrivateNetworkRule`. The arguments are
    the rule's, passed straight through: this class adds the vocabulary a crawl
    records a refusal in, and nothing else.
    """

    def __init__(
        self,
        *,
        allow: Iterable[str] = (),
        allow_private: bool = False,
        resolve: bool = True,
        resolver: Resolver | None = None,
    ) -> None:
        self._rule = PrivateNetworkRule(
            allow=allow,
            allow_private=allow_private,
            resolve=resolve,
            resolver=resolver,
        )

    @property
    def rule(self) -> PrivateNetworkRule:
        """Return the rule this policy speaks for."""
        return self._rule

    def may_fetch(self, url: str) -> PolicyDecision:
        """Return whether *url* points somewhere a stranger may send us."""
        reason = self._rule.refusal_for(url)
        if reason is None:
            return PolicyDecision.allow()
        return PolicyDecision.refuse(reason, rule=PolicyRule.PRIVATE_NETWORK)


def redirect_guard(policy: PrivateNetworkPolicy) -> Callable[[str], None]:
    """Return *policy* as the callable a fetcher checks each redirect with.

    The adapter exists so that :mod:`maxicrawler.web.fetcher` never imports a
    policy: it is handed a function that raises, which is all it needs to know.
    The decision stays here.
    """

    def guard(url: str) -> None:
        decision = policy.may_fetch(url)
        if decision.allowed:
            return
        message = f"redirected to {decision.reason or 'a private address'}"
        raise PolicyRefusedError(message, rule=PolicyRule.PRIVATE_NETWORK)

    return guard
