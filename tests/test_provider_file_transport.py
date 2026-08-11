"""Tests for the transport that reads what a host says about a file.

Against real sockets, real redirects and real headers rather than a stubbed
``urllib``. The redirect path is exactly where the guard has to hold, and a
mocked opener would prove nothing about it.

Every transport built here that is meant to succeed allows private addresses,
because the server under test is on 127.0.0.1 and the shipped default refuses
that. Saying so once, here, is the point: the tests that exercise the *guard*
build a transport without it, which is the shipped configuration.
"""

from collections.abc import Generator

import pytest
from web_server import Site, serve

from maxicrawler.library.naming import safe_filename
from maxicrawler.providers.errors import AddressRefusedError, ProviderTransportError
from maxicrawler.providers.transport import RemoteFile, UrllibFileTransport
from maxicrawler.utils.addresses import PrivateNetworkRule

PAYLOAD = b"x" * 3000


def make_transport(**kwargs: object) -> UrllibFileTransport:
    """Return a transport that may reach the local server."""
    kwargs.setdefault("rule", PrivateNetworkRule(allow_private=True))
    return UrllibFileTransport(user_agent="MaxiCrawler/test", timeout=5.0, **kwargs)  # type: ignore[arg-type]


def make_site(**kwargs: object) -> Site:
    """Return a site serving one ordinary file."""
    site = Site(**kwargs)  # type: ignore[arg-type]
    site.add("/file.bin", body=PAYLOAD, content_type="application/octet-stream")
    return site


def drain(chunks: Generator[bytes, None, None]) -> bytes:
    """Return everything *chunks* yields."""
    return b"".join(chunks)


# --- asking what is there ----------------------------------------------------


def test_a_head_reports_what_the_host_said() -> None:
    with serve(make_site()) as base:
        remote = make_transport().head(f"{base}/file.bin")

    assert remote.status == 200
    assert remote.ok is True
    assert remote.media_type == "application/octet-stream"
    assert remote.size == len(PAYLOAD)


def test_a_head_costs_no_body() -> None:
    """The whole reason to ask HEAD first."""
    site = make_site()

    with serve(site) as base:
        make_transport().head(f"{base}/file.bin")

    assert [request.path for request in site.requests] == ["/file.bin"]


def test_a_refusing_status_is_returned_rather_than_raised() -> None:
    """404 is an answer about the resource, and an inspection has a place for it."""
    with serve(make_site()) as base:
        remote = make_transport().head(f"{base}/missing.bin")

    assert remote.status == 404
    assert remote.ok is False


def test_a_host_that_will_not_answer_head_is_asked_the_way_it_does() -> None:
    """Plenty of real hosts answer 501 to HEAD. That is not "no such file"."""
    site = make_site(answers_head=False)

    with serve(site) as base:
        remote = make_transport().head(f"{base}/file.bin")

    assert remote.status == 200
    assert remote.size == len(PAYLOAD)
    assert [request.path for request in site.requests] == ["/file.bin", "/file.bin"]


def test_the_url_reported_is_the_one_that_answered() -> None:
    """What a name and a record should be built from, not the one asked for."""
    site = make_site()
    site.add("/go", status=302, location="/file.bin", body=b"")

    with serve(site) as base:
        remote = make_transport().head(f"{base}/go")

    assert remote.url == f"{base}/file.bin"


# --- the name a host states --------------------------------------------------


@pytest.mark.parametrize(
    ("disposition", "expected"),
    [
        ('attachment; filename="report.pdf"', "report.pdf"),
        ("attachment; filename=report.pdf", "report.pdf"),
        ("attachment; filename*=UTF-8''na%C3%AFve.pdf", "naïve.pdf"),
        ("inline", None),
        ("", None),
    ],
)
def test_a_stated_filename_is_read_including_the_encoded_form(
    disposition: str, expected: str | None
) -> None:
    """The RFC 2231 form is why this is parsed rather than split on quotes."""
    site = Site()
    site.add("/f", body=PAYLOAD, headers=(("Content-Disposition", disposition),))

    with serve(site) as base:
        remote = make_transport().head(f"{base}/f")

    assert remote.filename == expected


def test_no_disposition_states_no_name() -> None:
    with serve(make_site()) as base:
        assert make_transport().head(f"{base}/file.bin").filename is None


def test_a_hostile_filename_arrives_unsanitized_and_is_cleaned_downstream() -> None:
    """Two rules on one string would be one rule too many.

    The transport reports what the header said, faithfully. Cleaning it is the
    library's job and already was, for every name it stores -- so this asserts
    the pairing rather than adding a second sanitizer here.
    """
    site = Site()
    site.add(
        "/f",
        body=PAYLOAD,
        headers=(("Content-Disposition", 'attachment; filename="../../etc/passwd"'),),
    )

    with serve(site) as base:
        remote = make_transport().head(f"{base}/f")

    assert remote.filename == "../../etc/passwd"
    cleaned = safe_filename(remote.filename)
    assert "/" not in cleaned
    assert ".." not in cleaned


# --- what a host does not say ------------------------------------------------


def test_a_missing_length_is_unknown_rather_than_zero() -> None:
    site = Site()
    site.add("/f", body=PAYLOAD, omit_content_length=True)

    with serve(site) as base:
        assert make_transport().head(f"{base}/f").size is None


def test_an_unreadable_length_is_unknown_too() -> None:
    """Guessing would put a number in front of somebody no server ever sent."""
    site = Site()
    site.add("/f", body=PAYLOAD, headers=(("Content-Length", "banana"),), omit_content_length=True)

    with serve(site) as base:
        assert make_transport().head(f"{base}/f").size is None


def test_a_missing_content_type_is_unknown() -> None:
    site = Site()
    site.add("/f", body=PAYLOAD, content_type=None)

    with serve(site) as base:
        assert make_transport().head(f"{base}/f").media_type is None


# --- transferring ------------------------------------------------------------


def test_opening_yields_the_description_and_then_the_bytes() -> None:
    with serve(make_site()) as base:
        remote, chunks = make_transport().open(f"{base}/file.bin")
        body = drain(chunks)

    assert remote.size == len(PAYLOAD)
    assert body == PAYLOAD


def test_a_transfer_arrives_in_chunks_rather_than_whole() -> None:
    """Memory stays flat however large the file is."""
    with serve(make_site()) as base:
        _, chunks = make_transport().open(f"{base}/file.bin", chunk_size=1024)
        sizes = [len(chunk) for chunk in chunks]

    assert len(sizes) > 1
    assert max(sizes) <= 1024
    assert sum(sizes) == len(PAYLOAD)


def test_a_refusing_status_raises_when_content_was_wanted() -> None:
    """Unlike a head: there is no content to hand back and no partial answer."""
    with serve(make_site()) as base, pytest.raises(ProviderTransportError, match="404"):
        make_transport().open(f"{base}/missing.bin")


def test_an_abandoned_transfer_can_be_closed() -> None:
    """A caller that gives up part-way releases the socket by closing."""
    with serve(make_site()) as base:
        _, chunks = make_transport().open(f"{base}/file.bin", chunk_size=64)

        assert len(next(chunks)) == 64
        chunks.close()


def test_a_chunk_size_must_be_positive() -> None:
    with serve(make_site()) as base, pytest.raises(ValueError, match="chunk_size must be positive"):
        make_transport().open(f"{base}/file.bin", chunk_size=0)


# --- the guard ---------------------------------------------------------------


def test_the_default_transport_refuses_this_machine() -> None:
    """The shipped configuration, and the reason the rule is not optional here.

    A transport somebody wired without thinking about it is the safe one.
    """
    site = make_site()

    with serve(site) as base:
        transport = UrllibFileTransport(user_agent="MaxiCrawler/test", timeout=5.0)

        with pytest.raises(AddressRefusedError, match="not a public address"):
            transport.head(f"{base}/file.bin")


def test_a_refused_address_costs_no_request_at_all() -> None:
    """Refused before the socket, not after the answer."""
    site = make_site()

    with serve(site) as base:
        transport = UrllibFileTransport(user_agent="MaxiCrawler/test", timeout=5.0)
        with pytest.raises(AddressRefusedError):
            transport.open(f"{base}/file.bin")

    assert site.requests == []


def test_a_redirect_into_a_metadata_service_is_refused_mid_chain() -> None:
    """Where SSRF actually lives.

    The first URL is fine and the guard has already passed it. A check made
    only at the start would follow this without a word.
    """
    site = make_site()
    site.add("/go", status=302, location="http://169.254.169.254/latest/meta-data/", body=b"")

    with serve(site) as base:
        transport = make_transport()

        with pytest.raises(AddressRefusedError, match="cloud metadata service"):
            transport.head(f"{base}/go")


def test_a_metadata_service_stays_refused_though_private_networks_are_allowed() -> None:
    """Opening an intranet is not the decision to hand over a cloud credential."""
    site = make_site()
    site.add("/go", status=302, location="http://169.254.169.254/", body=b"")

    with serve(site) as base, pytest.raises(AddressRefusedError, match="metadata"):
        make_transport().head(f"{base}/go")


def test_a_refusal_is_a_transport_failure_to_whoever_only_catches_those() -> None:
    """No transfer happened, which is all a download manager needs to know."""
    with serve(make_site()) as base:
        transport = UrllibFileTransport(user_agent="MaxiCrawler/test", timeout=5.0)

        with pytest.raises(ProviderTransportError):
            transport.head(f"{base}/file.bin")


def test_a_chain_that_never_ends_is_cut() -> None:
    site = Site()
    site.add("/loop", status=302, location="/loop", body=b"")

    with serve(site) as base, pytest.raises(ProviderTransportError, match="redirects"):
        make_transport(max_redirects=3).head(f"{base}/loop")


def test_a_redirect_to_a_scheme_we_do_not_speak_is_refused() -> None:
    site = Site()
    site.add("/go", status=302, location="ftp://example.test/file", body=b"")

    with serve(site) as base, pytest.raises(ProviderTransportError):
        make_transport().head(f"{base}/go")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "not a url", "mailto:a@b.test"])
def test_a_url_that_is_not_http_never_reaches_a_socket(url: str) -> None:
    with pytest.raises(ProviderTransportError):
        make_transport().head(url)


def test_the_transport_names_the_rule_it_is_held_to() -> None:
    rule = PrivateNetworkRule(allow_private=True)

    assert make_transport(rule=rule).rule is rule


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"timeout": 0.0}, "timeout must be positive"),
        ({"max_redirects": -1}, "must not be negative"),
    ],
)
def test_impossible_settings_are_refused(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        UrllibFileTransport(user_agent="MaxiCrawler/test", **kwargs)  # type: ignore[arg-type]


def test_the_transport_satisfies_the_protocol_it_implements() -> None:
    from maxicrawler.providers.transport import FileTransport

    assert isinstance(make_transport(), FileTransport)


def test_a_remote_file_is_immutable() -> None:
    remote = RemoteFile(url="https://example.test/f", status=200)

    with pytest.raises(AttributeError):
        remote.status = 404  # type: ignore[misc]
