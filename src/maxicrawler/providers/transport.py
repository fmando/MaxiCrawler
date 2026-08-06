"""The seams through which MaxiCrawler issues HTTP requests.

Providers depend on :class:`HttpTransport` and :class:`StreamTransport` rather
than on a concrete HTTP library, so a test can drive a full inspection or a
full download without a socket and so the default implementations can be
swapped without touching provider logic.

The two are separate on purpose. An API call is a small JSON document that is
read into memory whole; a transfer is an unbounded stream of bytes that must
never be. Keeping them apart means the memory bound that protects the first can
stay in place, and a provider that only reads metadata never gains the ability
to move content.
"""

import json
from collections.abc import Generator, Mapping
from contextlib import closing
from typing import Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from maxicrawler.providers.errors import ProviderProtocolError, ProviderTransportError
from maxicrawler.utils import require_http_scheme, safe_target

DEFAULT_TIMEOUT = 30.0
"""Seconds to wait for a response before giving up."""

DEFAULT_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
"""Upper bound on a response body, so a hostile reply cannot exhaust memory."""

DEFAULT_CHUNK_SIZE = 1024 * 1024
"""Bytes read from a transfer at a time.

Large enough that the per-read overhead disappears against real throughput,
small enough that progress stays responsive and memory flat regardless of how
large the file is.
"""


@runtime_checkable
class HttpTransport(Protocol):
    """Sends a JSON document and returns the decoded JSON answer."""

    def post_json(
        self,
        url: str,
        payload: object,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> object:
        """POST *payload* as JSON to *url* and return the decoded response.

        Raises:
            ProviderTransportError: the request could not be carried out.
            ProviderProtocolError: the response was not decodable JSON.
        """
        ...


@runtime_checkable
class StreamTransport(Protocol):
    """Reads a response body in chunks, without ever holding all of it."""

    def stream(
        self, url: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE
    ) -> Generator[bytes, None, None]:
        """Yield the body of a GET request to *url*, chunk by chunk.

        The result is a generator rather than a bare iterator because the
        connection has to be releasable on demand: a caller that abandons a
        transfer part-way — because the disk filled up, or the user pressed
        Ctrl-C — closes the generator and the socket goes with it.

        Nothing happens until the first chunk is pulled, so building the
        generator is free and opening the connection is the caller's decision.

        Raises:
            ProviderTransportError: the request could not be carried out, or
                failed while the body was being read.
        """
        ...


class UrllibTransport:
    """An :class:`HttpTransport` built on the standard library.

    Using ``urllib`` keeps MaxiCrawler's runtime dependency footprint at a
    single package. The class is deliberately thin: it knows about sockets and
    JSON encoding, and nothing about any provider's API.
    """

    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float = DEFAULT_TIMEOUT,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if timeout <= 0:
            msg = "timeout must be positive"
            raise ValueError(msg)
        if max_response_bytes <= 0:
            msg = "max_response_bytes must be positive"
            raise ValueError(msg)
        self._user_agent = user_agent
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes

    def post_json(
        self,
        url: str,
        payload: object,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> object:
        """POST *payload* as JSON to *url* and return the decoded response."""
        request = self._build_request(url, payload, params, headers)
        try:
            with urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                body = response.read(self._max_response_bytes + 1)
        except HTTPError as error:
            msg = f"HTTP {error.code} from {safe_target(url)}"
            raise ProviderTransportError(msg) from error
        except (URLError, TimeoutError, OSError) as error:
            msg = f"request to {safe_target(url)} failed"
            raise ProviderTransportError(msg) from error
        if len(body) > self._max_response_bytes:
            msg = f"response from {safe_target(url)} exceeds {self._max_response_bytes} bytes"
            raise ProviderTransportError(msg)
        return decode_json(body, source=safe_target(url))

    def _build_request(
        self,
        url: str,
        payload: object,
        params: Mapping[str, str] | None,
        headers: Mapping[str, str] | None,
    ) -> Request:
        """Return the prepared request for *url*."""
        try:
            require_http_scheme(url)
        except ValueError as error:
            raise ProviderTransportError(str(error)) from error
        target = f"{url}?{urlencode(dict(params))}" if params else url
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self._user_agent,
        }
        request_headers.update(headers or {})
        return Request(target, data=body, headers=request_headers, method="POST")  # noqa: S310


class UrllibStreamTransport:
    """A :class:`StreamTransport` built on the standard library.

    Deliberately thin, like its sibling: it knows about sockets and chunk
    sizes, and nothing about what the bytes mean. Whether the content is
    encrypted, compressed, or plain is entirely the provider's business.

    No response-size limit applies here. A transfer is expected to be large,
    and it is written straight to disk rather than accumulated, so the bound
    that protects :class:`UrllibTransport` would only get in the way.
    """

    def __init__(self, *, user_agent: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        if timeout <= 0:
            msg = "timeout must be positive"
            raise ValueError(msg)
        self._user_agent = user_agent
        self._timeout = timeout

    def stream(
        self, url: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE
    ) -> Generator[bytes, None, None]:
        """Yield the body of a GET request to *url*, chunk by chunk."""
        if chunk_size <= 0:
            msg = "chunk_size must be positive"
            raise ValueError(msg)
        try:
            require_http_scheme(url)
        except ValueError as error:
            raise ProviderTransportError(str(error)) from error
        request = Request(  # noqa: S310 - the scheme is checked above
            url,
            headers={"User-Agent": self._user_agent, "Accept": "*/*"},
            method="GET",
        )
        try:
            response = urlopen(request, timeout=self._timeout)  # noqa: S310
        except HTTPError as error:
            msg = f"HTTP {error.code} from {safe_target(url)}"
            raise ProviderTransportError(msg) from error
        except (URLError, TimeoutError, OSError) as error:
            msg = f"request to {safe_target(url)} failed"
            raise ProviderTransportError(msg) from error
        with closing(response):
            while True:
                try:
                    chunk = response.read(chunk_size)
                except (URLError, TimeoutError, OSError) as error:
                    msg = f"transfer from {safe_target(url)} was interrupted"
                    raise ProviderTransportError(msg) from error
                if not chunk:
                    return
                yield chunk


def decode_json(body: bytes, *, source: str) -> object:
    """Return the JSON document in *body*.

    Raises:
        ProviderProtocolError: *body* is not decodable JSON, which means the
            remote API changed shape rather than that the request failed.
    """
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        msg = f"response from {source} is not valid JSON"
        raise ProviderProtocolError(msg) from error
