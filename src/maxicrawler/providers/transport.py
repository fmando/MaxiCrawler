"""The single seam through which MaxiCrawler issues HTTP requests.

Providers depend on :class:`HttpTransport` rather than on a concrete HTTP
library, so a test can drive a full inspection without a socket and so the
default implementation can be swapped without touching provider logic.
"""

import json
from collections.abc import Mapping
from typing import Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from maxicrawler.providers.errors import ProviderProtocolError, ProviderTransportError

DEFAULT_TIMEOUT = 30.0
"""Seconds to wait for a response before giving up."""

DEFAULT_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
"""Upper bound on a response body, so a hostile reply cannot exhaust memory."""

ALLOWED_SCHEMES = frozenset({"http", "https"})
"""Schemes a transport is willing to talk; anything else is a configuration bug."""


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
            msg = f"HTTP {error.code} from {_safe_target(url)}"
            raise ProviderTransportError(msg) from error
        except (URLError, TimeoutError, OSError) as error:
            msg = f"request to {_safe_target(url)} failed"
            raise ProviderTransportError(msg) from error
        if len(body) > self._max_response_bytes:
            msg = f"response from {_safe_target(url)} exceeds {self._max_response_bytes} bytes"
            raise ProviderTransportError(msg)
        return decode_json(body, source=_safe_target(url))

    def _build_request(
        self,
        url: str,
        payload: object,
        params: Mapping[str, str] | None,
        headers: Mapping[str, str] | None,
    ) -> Request:
        """Return the prepared request for *url*."""
        scheme = urlsplit(url).scheme.lower()
        if scheme not in ALLOWED_SCHEMES:
            msg = f"unsupported URL scheme: {scheme or '(none)'}"
            raise ProviderTransportError(msg)
        target = f"{url}?{urlencode(dict(params))}" if params else url
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self._user_agent,
        }
        request_headers.update(headers or {})
        return Request(target, data=body, headers=request_headers, method="POST")  # noqa: S310


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


def _safe_target(url: str) -> str:
    """Return *url* reduced to scheme, host, and path.

    Query strings and fragments are dropped so that no identifier or credential
    can reach an exception message through a failed request.
    """
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
