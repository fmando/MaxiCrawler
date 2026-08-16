"""Extra headers on a file transport, and where they stop.

Against real sockets on purpose. Whether a header reaches the wire is a fact
about ``urllib``, and whether it survives a redirect is a fact about
``HTTPRedirectHandler`` — the base class copies the original request's headers
onto the request it builds for the next hop, stripping only the content ones.
For a header that authorises a request that is the difference between a working
download and a credential handed to whoever the redirect pointed at, and
neither half of it survives being mocked.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from web_server import Site, serve

from maxicrawler.providers.transport import UrllibFileTransport
from maxicrawler.utils.addresses import PrivateNetworkRule

SESSION = "mu_sid=s3cr3t"
PAYLOAD = b"a file\n"


def loopback_rule() -> PrivateNetworkRule:
    """Return a rule that permits the throwaway server."""
    return PrivateNetworkRule(allow=("127.0.0.1",), allow_private=True)


def transport(*, host: str | None) -> UrllibFileTransport:
    """Return a transport carrying the session to *host* alone.

    Shaped exactly like the function ``app/downloading.py`` builds from a
    :class:`~maxicrawler.web.cookies.CookieJar`: a decision made from the URL,
    at the moment the URL is known.
    """

    def headers(url: str) -> dict[str, str]:
        if host is None or not url.startswith(host):
            return {}
        return {"Cookie": SESSION}

    return UrllibFileTransport(user_agent="test-agent", rule=loopback_rule(), extra_headers=headers)


@contextmanager
def two_sites() -> Iterator[tuple[Site, str, Site, str]]:
    """Serve two independent hosts and yield both with their base URLs."""
    first, second = Site(), Site()
    with serve(first) as one, serve(second) as two:
        yield first, one, second, two


def test_an_extra_header_reaches_the_wire() -> None:
    site = Site()
    site.add("/file", body=PAYLOAD)

    with serve(site) as base:
        UrllibFileTransport(
            user_agent="test-agent",
            rule=loopback_rule(),
            extra_headers=lambda _: {"Cookie": SESSION},
        ).head(f"{base}/file")

    assert site.requests[0].headers.get("Cookie") == SESSION


def test_a_transport_without_extra_headers_sends_none() -> None:
    """The default must stay anonymous; this is the transport crawls use."""
    site = Site()
    site.add("/file", body=PAYLOAD)

    with serve(site) as base:
        UrllibFileTransport(user_agent="test-agent", rule=loopback_rule()).head(f"{base}/file")

    assert "Cookie" not in site.requests[0].headers


def test_a_host_the_source_declines_gets_nothing() -> None:
    """The decision is per URL, which is what confines a credential to one host."""
    with two_sites() as (first, one, second, two):
        first.add("/file", body=PAYLOAD)
        second.add("/file", body=PAYLOAD)
        moving = transport(host=one)
        moving.head(f"{one}/file")
        moving.head(f"{two}/file")

    assert first.requests[0].headers.get("Cookie") == SESSION
    assert "Cookie" not in second.requests[0].headers


def test_the_header_does_not_follow_a_redirect_off_the_host() -> None:
    """The leak this seam exists to prevent.

    Without re-deciding per hop, ``urllib`` would carry the session onto the
    request for the next host — which is a credential handed to whoever
    controls the redirect target.
    """
    with two_sites() as (first, one, second, two):
        first.add("/start", status=302, location=f"{two}/file", body=b"")
        second.add("/file", body=PAYLOAD)
        _, chunks = transport(host=one).open(f"{one}/start")
        list(chunks)

    assert second.requests
    assert "Cookie" not in second.requests[0].headers


def test_the_header_survives_a_redirect_that_stays_on_the_host() -> None:
    """Confinement must not become breakage: one host's own hops still carry it.

    MuseScore redirects a download URL to a storage path on the same host, so a
    handler that stripped the header from every hop would break the ordinary
    case while fixing the dangerous one.
    """
    site = Site()
    site.add("/start", status=302, location="/file", body=b"")
    site.add("/file", body=PAYLOAD)

    with serve(site) as base:
        _, chunks = transport(host=base).open(f"{base}/start")
        payload = b"".join(chunks)

    assert payload == PAYLOAD
    assert [request.headers.get("Cookie") for request in site.requests] == [SESSION, SESSION]


def test_a_redirect_back_onto_the_host_regains_the_header() -> None:
    """Off and on again: the header is decided by the address, not by history."""
    with two_sites() as (first, one, second, two):
        first.add("/start", status=302, location=f"{two}/away", body=b"")
        second.add("/away", status=302, location=f"{one}/file", body=b"")
        first.add("/file", body=PAYLOAD)
        _, chunks = transport(host=one).open(f"{one}/start")
        list(chunks)

    assert "Cookie" not in second.requests[0].headers
    assert first.requests[-1].headers.get("Cookie") == SESSION
