"""A throwaway HTTP server for the web layer's tests.

Generalised from the pattern in ``tests/test_provider_transport.py``. It exists
so the fetcher can be exercised against real sockets, real redirects, and real
compressed bodies rather than against a mocked ``urllib``: the redirect and
decompression paths are exactly where the bugs live, and neither survives being
stubbed out.

Nothing here reaches beyond ``127.0.0.1``.
"""

import gzip
import threading
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class RecordedRequest:
    """One request the local server received."""

    path: str
    headers: dict[str, str]


@dataclass
class Route:
    """What the server should answer for one path."""

    body: bytes = b"<html></html>"
    status: int = 200
    content_type: str | None = "text/html; charset=utf-8"
    content_encoding: str | None = None
    location: str | None = None
    """Sent as the ``Location`` header, making the response a redirect."""

    content_length: int | None = None
    """Overrides the announced length, so a lying header can be tested."""

    omit_content_length: bool = False


@dataclass
class Site:
    """The routes a local server serves, and what it was asked for."""

    routes: dict[str, Route] = field(default_factory=dict)
    requests: list[RecordedRequest] = field(default_factory=list)
    default: Route = field(default_factory=lambda: Route(status=404, body=b"nope"))

    def add(self, path: str, **kwargs: object) -> None:
        """Register a route; keyword arguments are :class:`Route` fields."""
        self.routes[path] = Route(**kwargs)  # type: ignore[arg-type]

    def add_html(self, path: str, markup: str, **kwargs: object) -> None:
        """Register an HTML route holding *markup*."""
        self.add(path, body=markup.encode("utf-8"), **kwargs)

    def route_for(self, path: str) -> Route:
        """Return the route registered for *path*, or the fallback."""
        return self.routes.get(path, self.default)


class _Handler(BaseHTTPRequestHandler):
    """Serves the configured site and records what was asked for."""

    site: Site
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        self.site.requests.append(RecordedRequest(path=self.path, headers=dict(self.headers)))
        route = self.site.route_for(self.path)
        self.send_response(route.status)
        if route.location is not None:
            self.send_header("Location", route.location)
        if route.content_type is not None:
            self.send_header("Content-Type", route.content_type)
        if route.content_encoding is not None:
            self.send_header("Content-Encoding", route.content_encoding)
        if not route.omit_content_length:
            announced = route.content_length
            self.send_header(
                "Content-Length", str(len(route.body) if announced is None else announced)
            )
        self.end_headers()
        self.wfile.write(route.body)

    def log_message(self, format: str, *args: object) -> None:
        """Silence the default stderr logging."""


@contextmanager
def serve(site: Site) -> Iterator[str]:
    """Run *site* on a free port and yield its base URL."""
    handler = type("_ConfiguredHandler", (_Handler,), {"site": site})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def gzipped(payload: bytes) -> bytes:
    """Return *payload* as a gzip stream."""
    return gzip.compress(payload)


def deflated(payload: bytes, *, raw: bool = False) -> bytes:
    """Return *payload* as a deflate stream, zlib-framed or raw."""
    if not raw:
        return zlib.compress(payload)
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    return compressor.compress(payload) + compressor.flush()
