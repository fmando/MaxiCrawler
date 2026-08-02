"""Tests for the HTTP transport, exercised against a local server."""

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from maxicrawler.providers import (
    HttpTransport,
    ProviderProtocolError,
    ProviderTransportError,
    UrllibTransport,
)
from maxicrawler.providers.transport import decode_json


@dataclass
class RecordedRequest:
    """One request the local server received."""

    path: str
    body: bytes
    headers: dict[str, str]

    @property
    def payload(self) -> Any:
        """Return the decoded JSON body."""
        return json.loads(self.body.decode("utf-8"))


@dataclass
class ServerBehaviour:
    """What the local server should answer with."""

    status: int = 200
    body: bytes = b'{"answer": 42}'
    requests: list[RecordedRequest] = field(default_factory=list)


class _Handler(BaseHTTPRequestHandler):
    """Records the request and replies with the configured behaviour."""

    behaviour: ServerBehaviour

    def do_POST(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length", "0"))
        self.behaviour.requests.append(
            RecordedRequest(
                path=self.path,
                body=self.rfile.read(length),
                headers=dict(self.headers),
            )
        )
        self.send_response(self.behaviour.status)
        self.send_header("Content-Type", "application/json")
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
        yield f"http://127.0.0.1:{httpd.server_address[1]}/cs", behaviour
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def make_transport() -> UrllibTransport:
    """Return a transport with a short timeout so failures stay quick."""
    return UrllibTransport(user_agent="MaxiCrawler/test", timeout=5.0)


def test_transport_satisfies_the_runtime_protocol() -> None:
    assert isinstance(make_transport(), HttpTransport)


def test_transport_posts_json_and_decodes_the_answer(
    server: tuple[str, ServerBehaviour],
) -> None:
    url, behaviour = server

    answer = make_transport().post_json(url, [{"a": "g"}])

    assert answer == {"answer": 42}
    assert behaviour.requests[0].payload == [{"a": "g"}]


def test_transport_sends_the_configured_user_agent(
    server: tuple[str, ServerBehaviour],
) -> None:
    url, behaviour = server

    make_transport().post_json(url, {})

    headers = behaviour.requests[0].headers
    assert headers["User-Agent"] == "MaxiCrawler/test"
    assert headers["Content-Type"] == "application/json"


def test_transport_appends_query_parameters(server: tuple[str, ServerBehaviour]) -> None:
    url, behaviour = server

    make_transport().post_json(url, {}, params={"id": "7", "n": "AaBbCcDd"})

    assert behaviour.requests[0].path == "/cs?id=7&n=AaBbCcDd"


def test_transport_merges_extra_headers(server: tuple[str, ServerBehaviour]) -> None:
    url, behaviour = server

    make_transport().post_json(url, {}, headers={"X-Trace": "abc"})

    assert behaviour.requests[0].headers["X-Trace"] == "abc"


def test_transport_reports_an_error_status_as_a_transport_failure(
    server: tuple[str, ServerBehaviour],
) -> None:
    url, behaviour = server
    behaviour.status = 503

    with pytest.raises(ProviderTransportError, match="HTTP 503"):
        make_transport().post_json(url, {})


def test_transport_reports_an_unreadable_body_as_a_protocol_failure(
    server: tuple[str, ServerBehaviour],
) -> None:
    url, behaviour = server
    behaviour.body = b"<html>not json</html>"

    with pytest.raises(ProviderProtocolError, match="not valid JSON"):
        make_transport().post_json(url, {})


def test_transport_rejects_an_oversized_response(
    server: tuple[str, ServerBehaviour],
) -> None:
    url, behaviour = server
    behaviour.body = b'"' + b"x" * 64 + b'"'
    transport = UrllibTransport(user_agent="MaxiCrawler/test", timeout=5.0, max_response_bytes=16)

    with pytest.raises(ProviderTransportError, match="exceeds 16 bytes"):
        transport.post_json(url, {})


def test_transport_reports_an_unreachable_host() -> None:
    with pytest.raises(ProviderTransportError, match="failed"):
        make_transport().post_json("http://127.0.0.1:1/cs", {})


def test_transport_rejects_a_non_http_scheme() -> None:
    with pytest.raises(ProviderTransportError, match="unsupported URL scheme: file"):
        make_transport().post_json("file:///etc/passwd", {})


def test_transport_keeps_query_parameters_out_of_error_messages() -> None:
    with pytest.raises(ProviderTransportError) as failure:
        make_transport().post_json("http://127.0.0.1:1/cs", {}, params={"n": "SecretHandle"})

    assert "SecretHandle" not in str(failure.value)


def test_transport_rejects_a_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout must be positive"):
        UrllibTransport(user_agent="MaxiCrawler/test", timeout=0)


def test_transport_rejects_a_non_positive_response_limit() -> None:
    with pytest.raises(ValueError, match="max_response_bytes must be positive"):
        UrllibTransport(user_agent="MaxiCrawler/test", max_response_bytes=0)


def test_decode_json_reports_undecodable_bytes() -> None:
    with pytest.raises(ProviderProtocolError, match="not valid JSON"):
        decode_json(b"\xff\xfe", source="https://example.test/cs")


def test_decode_json_returns_the_document() -> None:
    assert decode_json(b'[{"a": 1}]', source="https://example.test/cs") == [{"a": 1}]
