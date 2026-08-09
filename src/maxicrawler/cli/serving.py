"""Deciding where the web interface may listen, and saying so.

Every function here is pure, which is the point: whether a bind is allowed and
what the operator is told about it are exactly the parts worth testing, and
neither of them should need a socket to check.

**The interface has no authentication.** Anyone who can reach the port can
start a crawl with it, and a crawl is an outbound request made from this
machine. That is fine on a loopback address, where "anyone" means whoever is
already logged in here. It is a different proposition on a network, so binding
anywhere else is refused unless it was asked for in as many words.

The refusal is not security — a flag stops nobody determined. It is the
difference between exposing a service and doing it by accident, which is the
failure that actually happens.
"""

import ipaddress

EXIT_WEB_UNAVAILABLE = 8
"""The web interface was asked for and is not installed."""

LOOPBACK_NAMES = frozenset({"localhost"})
"""Names taken to mean this machine without asking a resolver.

Only this one. A hostname can resolve anywhere, and a resolver answering
differently later would quietly turn a local server into a public one, so
anything not on this list is treated as remote and needs the flag. Erring
toward asking is the cheap mistake.
"""

EVERY_INTERFACE = frozenset({"0.0.0.0", "::", ""})  # noqa: S104 - named to be refused, not used
"""Addresses that bind every interface there is, loopback among them."""


def is_loopback(host: str) -> bool:
    """Return whether *host* can only be reached from this machine."""
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Not an address at all, so a name we have no business resolving.
        return False


def url_for(host: str, port: int) -> str:
    """Return the address to type into a browser on this machine."""
    if host in EVERY_INTERFACE:
        return f"http://localhost:{port}/"
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        return f"http://{host}:{port}/"
    return f"http://[{host}]:{port}/" if literal.version == 6 else f"http://{host}:{port}/"


def banner(host: str, port: int) -> str:
    """Return what to print as the server comes up."""
    where = url_for(host, port)
    if host in EVERY_INTERFACE:
        return f"MaxiCrawler is listening on every interface, port {port}\nFrom here: {where}"
    return f"MaxiCrawler is listening on {where}"


def refusal(host: str) -> str:
    """Return why a bind was refused, and what to do about it."""
    return (
        f"{host} is not a loopback address, so this would be reachable from "
        "elsewhere on the network.\n"
        "The web interface has no authentication and can start crawls, so that "
        "has to be deliberate.\n"
        "Pass --allow-remote if it is."
    )


def exposure_notice(host: str, port: int) -> str:
    """Return the warning that goes with a bind somebody asked to allow.

    Printed every time rather than once, because the flag is typed once and
    read by whoever is looking at the terminal afterwards.
    """
    reach = "any interface of this machine" if host in EVERY_INTERFACE else host
    return (
        f"Warning: reachable through {reach} on port {port}, with no "
        "authentication.\n"
        "         Anyone who can reach it can start crawls from this machine."
    )
