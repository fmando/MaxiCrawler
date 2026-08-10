"""Keeping a crawl off this machine, this network, and the metadata service.

Until Sprint 11 the only person who could name a URL was the person running
the program, and a program that fetches what its operator typed is doing its
job. A web interface changes that: a URL now arrives from a browser, and a
browser can be pointed at a form by any page it visits. ``http://localhost:…``
and ``http://169.254.169.254/…`` stop being odd and start being an attack.

The whole of the defence is a :class:`~maxicrawler.web.policy.CrawlPolicy`.
Neither the engine nor the fetcher learns what an internal address is; they ask
and are told, exactly as they already are about scope and robots.txt.

**Two checks, because they cost different amounts.**

*   The **literal** check reads the URL: ``localhost``, ``*.internal``,
    ``10.0.0.1``, ``[::1]``. It is pure, so it runs at the engine's first gate
    and a page full of links to this machine never reaches the frontier.
*   The **resolved** check asks the resolver. It is what catches
    ``metadata.google.internal``, an intranet name, and the free DNS services
    that answer with ``127.0.0.1`` for anything. It costs a lookup, so it runs
    at the second gate, immediately before the request.

**And the redirect, which is where SSRF actually lives.** A public URL that
answers ``302 Location: http://169.254.169.254/`` would walk straight past a
check made once at the start. So the fetcher calls a guard on every hop; the
guard is this policy, adapted to a plain callable by :func:`redirect_guard` so
that the fetcher never imports a policy.

**What this does not do.** Between our lookup and ``urllib``'s there is a
second lookup, and a name that answers differently each time — DNS rebinding —
can pass the first and be used by the second. Closing that means pinning the
address we checked onto the connection that is actually opened, which is a
change to how sockets are made rather than to a policy. It is named here
rather than left to be discovered: this raises the cost of reaching an internal
address, and does not make it impossible.
"""

import socket
from collections.abc import Callable, Iterable
from ipaddress import (
    AddressValueError,
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_network,
)
from threading import Lock
from urllib.parse import urlsplit

from maxicrawler.web.errors import PolicyRefusedError
from maxicrawler.web.policy import PolicyDecision, PolicyRule

Resolver = Callable[[str], tuple[str, ...]]
"""Turns a host name into the addresses it answers with."""

Address = IPv4Address | IPv6Address
Network = IPv4Network | IPv6Network

LOCAL_NAMES = frozenset({"localhost"})
"""Names that mean this machine without anybody having to be asked."""

LOCAL_SUFFIXES = ("localhost", ".localhost", ".local", ".internal", ".home.arpa")
"""Name endings that mean a network rather than the internet.

``.local`` is mDNS, ``.internal`` and ``.home.arpa`` are the reserved names for
private zones. Matched before a resolver is consulted, because a name that
means "inside" should not need a lookup to be refused.
"""

METADATA_NAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
    }
)
"""Host names that resolve to a cloud instance's credential service."""

METADATA_ADDRESSES = frozenset(
    {
        ip_address("169.254.169.254"),
        ip_address("169.254.170.2"),
        ip_address("100.100.100.200"),
        ip_address("fd00:ec2::254"),
    }
)
"""The addresses that hand out credentials to whoever asks from the instance.

AWS, GCP, Azure, DigitalOcean and Oracle share ``169.254.169.254``; the second
is the AWS container credential endpoint, the third is Alibaba's, and the last
is AWS over IPv6. These stay refused even when private addresses are allowed:
somebody who opens their intranet to a crawler has not thereby volunteered
their cloud credentials, and the two are one setting only by accident of both
being "not the public internet".
"""


def resolve_host(host: str) -> tuple[str, ...]:
    """Return every address *host* answers with, or nothing when it answers none.

    A name that does not resolve is not evidence of anything: the fetch that
    follows will fail on its own and say so properly. Refusing here would
    report a typo as an attempt to reach a private network.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return ()
    return tuple(str(info[4][0]) for info in infos)


def parse_address(host: str) -> Address | None:
    """Return *host* as an address when it is written as one.

    :mod:`ipaddress` is strict on purpose and rejects ``127.1``, ``0x7f.0.0.1``
    and ``2130706433``. The C resolver every socket eventually goes through
    accepts all three and reaches ``127.0.0.1`` with them, so a check that
    believed only :mod:`ipaddress` would call them host names, find nothing,
    and permit the fetch — the classic way past a guard like this.

    So the strict reading is tried first, and anything it declines is offered
    to :func:`socket.inet_aton`, which is the same interpretation the eventual
    connection will use. Matching what will really happen is the whole
    requirement here; being stricter or more lenient than the socket both end
    in the same kind of hole.
    """
    try:
        return ip_address(host)
    except (ValueError, AddressValueError):
        pass
    try:
        return ip_address(socket.inet_aton(host))
    except (OSError, ValueError):
        return None


def unwrap(address: Address) -> Address:
    """Return the IPv4 address inside an IPv4-mapped IPv6 one.

    ``::ffff:127.0.0.1`` is loopback wearing a hat, and a check that looked
    only at the outer form would let it through.
    """
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped if mapped is not None else address


def is_internal(address: Address) -> bool:
    """Return whether *address* is anything other than the public internet.

    ``is_global`` is the one question worth asking, and it is the standard
    library's rather than a list of ours: loopback, RFC 1918, link-local,
    carrier-grade NAT, unique local, multicast, reserved and unspecified are
    all not-global, and a range added to that set by a future RFC arrives with
    Python rather than needing to be noticed here.
    """
    return not unwrap(address).is_global


def is_metadata(address: Address) -> bool:
    """Return whether *address* is a cloud metadata service."""
    return unwrap(address) in METADATA_ADDRESSES or address in METADATA_ADDRESSES


def _named_internally(host: str) -> bool:
    """Return whether *host* is a name that means "not the internet"."""
    lowered = host.lower().rstrip(".")
    return lowered in LOCAL_NAMES or lowered.endswith(LOCAL_SUFFIXES)


def _parse_allowance(entry: str) -> Network | str:
    """Return an allowlist entry as the network or the host name it names."""
    try:
        return ip_network(entry, strict=False)
    except ValueError:
        return entry.strip().lower().rstrip(".")


class PrivateNetworkPolicy:
    """Refuses URLs that point inside, rather than out.

    The default refuses every non-public address and every name that resolves
    to one. ``allow_private`` opens the private ranges for an operator who
    means to crawl their own network — and opens *only* those: a metadata
    service is still refused, because allowing an intranet is not the same
    decision as handing over a cloud credential.

    An entry in *allow* is the fine-grained escape and beats everything: it may
    be a host name, an address, or a CIDR block. That is what makes crawling
    one machine on a home network, or a test server on loopback, possible
    without turning the whole guard off.
    """

    def __init__(
        self,
        *,
        allow: Iterable[str] = (),
        allow_private: bool = False,
        resolve: bool = True,
        resolver: Resolver | None = None,
    ) -> None:
        allowances = [_parse_allowance(entry) for entry in allow if entry.strip()]
        self._networks = tuple(item for item in allowances if not isinstance(item, str))
        self._names = frozenset(item for item in allowances if isinstance(item, str))
        self._allow_private = allow_private
        self._resolve = resolve
        self._resolver = resolver if resolver is not None else resolve_host
        self._lock = Lock()
        self._resolved: dict[str, tuple[str, ...]] = {}

    def may_fetch(self, url: str) -> PolicyDecision:
        """Return whether *url* points somewhere a stranger may send us."""
        host = urlsplit(url).hostname
        if not host:
            # No host to judge. Refusing it here would report the wrong reason;
            # nothing can be fetched from it either way.
            return PolicyDecision.allow()
        reason = self._verdict(host)
        if reason is None:
            return PolicyDecision.allow()
        return PolicyDecision.refuse(reason, rule=PolicyRule.PRIVATE_NETWORK)

    def _verdict(self, host: str) -> str | None:
        """Return why *host* is refused, or ``None`` when it is not.

        The order is the policy: an explicit allowance beats everything, a
        metadata service is refused even when private addresses are welcome,
        and the general private-address rule is last because it is the one an
        operator can turn off.
        """
        literal = parse_address(host)
        if self._allowed(host, literal):
            return None
        if host.lower().rstrip(".") in METADATA_NAMES:
            return f"{host} is a cloud metadata service"
        if literal is not None:
            return self._address_verdict(literal, named=host)
        if _named_internally(host):
            return f"{host} names this machine or this network"
        return self._resolved_verdict(host)

    def _resolved_verdict(self, host: str) -> str | None:
        """Return why the addresses *host* answers with are refused, if they are.

        Every answer is judged, not the first: a name that returns one public
        address and one loopback address is a name that can be used to reach
        loopback.
        """
        if not self._resolve:
            return None
        for value in self._addresses(host):
            address = parse_address(value)
            if address is None:
                continue
            if self._allowed(value, address):
                continue
            refusal = self._address_verdict(address, named=host)
            if refusal is not None:
                return refusal
        return None

    def _address_verdict(self, address: Address, *, named: str) -> str | None:
        """Return why *address* is refused, or ``None`` when it is not."""
        where = f"{named} ({address})" if named != str(address) else str(address)
        if is_metadata(address):
            return f"{where} is a cloud metadata service"
        if self._allow_private:
            return None
        if is_internal(address):
            return f"{where} is not a public address"
        return None

    def _allowed(self, host: str, address: Address | None) -> bool:
        """Return whether something explicitly allowed covers this."""
        if host.lower().rstrip(".") in self._names:
            return True
        if address is None:
            return False
        return any(address in network for network in self._networks if _fits(address, network))

    def _addresses(self, host: str) -> tuple[str, ...]:
        """Return what *host* resolves to, asking the resolver once."""
        with self._lock:
            cached = self._resolved.get(host)
        if cached is not None:
            return cached
        answers = self._resolver(host)
        with self._lock:
            self._resolved.setdefault(host, answers)
            return self._resolved[host]


def _fits(address: Address, network: Network) -> bool:
    """Return whether *address* and *network* are the same family."""
    return address.version == network.version


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
