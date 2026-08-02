"""Tests for the streaming transport, exercised against a local server."""

import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from maxicrawler.providers import (
    ProviderTransportError,
    StreamTransport,
    UrllibStreamTransport,
)

CONTENT = bytes(range(256)) * 40


@dataclass
class ServerBehaviour:
    """What the local server should answer a transfer with."""

    status: int = 200
    body: bytes = CONTENT
    paths: list[str] = field(default_factory=list)
    user_agents: list[str] = field(default_factory=list)


class _Handler(BaseHTTPRequestHandler):
    """Serves the configured bytes for a GET request."""

    behaviour: ServerBehaviour

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        self.behaviour.paths.append(self.path)
        self.behaviour.user_agents.append(self.headers.get("User-Agent", ""))
        self.send_response(self.behaviour.status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(self.behaviour.body)))
        self.end_headers()
        self.wfile.write(self.behaviour.body)

    def log_message(self, format: str, *args: object) -> None:
        """Silence the default stderr logging."""


@pytest.fixture
def server() -> Iterator[tuple[str, ServerBehaviour]]:
    """Run a throwaway HTTP server and yield its URL and behaviour."""
    behaviour = ServerBehaviour()
    handler = type("_ConfiguredHandler", (_Handler,), {"behaviour": behaviour})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/dl/AaBbCcDd", behaviour
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def make_transport() -> UrllibStreamTransport:
    """Return a transport with a short timeout so failures stay quick."""
    return UrllibStreamTransport(user_agent="MaxiCrawler/test", timeout=5.0)


def test_the_transport_satisfies_the_runtime_protocol() -> None:
    assert isinstance(make_transport(), StreamTransport)


def test_a_body_arrives_complete_and_in_order(server: tuple[str, ServerBehaviour]) -> None:
    url, _ = server

    received = b"".join(make_transport().stream(url))

    assert received == CONTENT


def test_a_body_arrives_in_chunks_of_the_requested_size(
    server: tuple[str, ServerBehaviour],
) -> None:
    url, _ = server

    chunks = list(make_transport().stream(url, chunk_size=1024))

    assert len(chunks) > 1
    assert all(len(chunk) <= 1024 for chunk in chunks)
    assert b"".join(chunks) == CONTENT


def test_an_empty_body_yields_no_chunk(server: tuple[str, ServerBehaviour]) -> None:
    url, behaviour = server
    behaviour.body = b""

    assert list(make_transport().stream(url)) == []


def test_the_configured_user_agent_is_sent(server: tuple[str, ServerBehaviour]) -> None:
    url, behaviour = server

    list(make_transport().stream(url))

    assert behaviour.user_agents == ["MaxiCrawler/test"]


def test_no_request_is_made_before_the_stream_is_read(
    server: tuple[str, ServerBehaviour],
) -> None:
    url, behaviour = server

    chunks = make_transport().stream(url)

    assert behaviour.paths == []
    next(chunks)
    assert behaviour.paths == ["/dl/AaBbCcDd"]
    chunks.close()


def test_an_error_status_is_reported_as_a_transport_failure(
    server: tuple[str, ServerBehaviour],
) -> None:
    url, behaviour = server
    behaviour.status = 509

    with pytest.raises(ProviderTransportError, match="HTTP 509"):
        list(make_transport().stream(url))


def test_an_unreachable_host_is_reported() -> None:
    with pytest.raises(ProviderTransportError, match="failed"):
        list(make_transport().stream("http://127.0.0.1:1/dl/AaBbCcDd"))


def test_a_non_http_scheme_is_refused() -> None:
    with pytest.raises(ProviderTransportError, match="unsupported URL scheme: file"):
        list(make_transport().stream("file:///etc/passwd"))


def test_a_failure_message_keeps_the_query_string_out() -> None:
    with pytest.raises(ProviderTransportError) as failure:
        list(make_transport().stream("http://127.0.0.1:1/dl?token=SecretHandle"))

    assert "SecretHandle" not in str(failure.value)


def test_a_non_positive_timeout_is_rejected() -> None:
    with pytest.raises(ValueError, match="timeout must be positive"):
        UrllibStreamTransport(user_agent="MaxiCrawler/test", timeout=0)


def test_a_non_positive_chunk_size_is_rejected(server: tuple[str, ServerBehaviour]) -> None:
    url, _ = server

    with pytest.raises(ValueError, match="chunk_size must be positive"):
        list(make_transport().stream(url, chunk_size=0))
