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
from collections.abc import Callable, Generator, Mapping
from contextlib import closing
from dataclasses import dataclass
from email.message import EmailMessage
from http.client import HTTPResponse
from typing import Any, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from maxicrawler.providers.errors import (
    AddressRefusedError,
    ProviderProtocolError,
    ProviderTransportError,
)
from maxicrawler.utils import require_http_scheme, safe_target
from maxicrawler.utils.addresses import PrivateNetworkRule

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


DEFAULT_MAX_REDIRECTS = 5
"""How many hops a chain may take before it is treated as a loop."""

NO_METHOD_STATUSES = frozenset({405, 501})
"""What a host answers when it will not do HEAD.

405 is "method not allowed" and 501 is "not implemented". Both mean *ask
differently*, not *the resource is not there*, so both are worth one retry with
a GET rather than being reported as a failure.
"""


@dataclass(frozen=True, slots=True)
class RemoteFile:
    """What a response said about the thing behind a URL.

    A faithful description of what came back and nothing more. In particular
    :attr:`filename` is what ``Content-Disposition`` stated, or ``None`` — the
    last path segment of a URL is a *guess* about a name, and mixing a guess
    into a record of what a server said would leave nobody able to tell them
    apart. Whoever needs a name makes that guess itself.
    """

    url: str
    """The URL that finally answered, after every redirect."""

    status: int
    media_type: str | None = None
    """The type the host stated, lowercased and without its parameters."""

    size: int | None = None
    """``Content-Length``, when the host stated one it could be read as.

    ``None`` means *unknown*, never zero. A chunked response states no length
    at all, which is normal and not a fault.
    """

    filename: str | None = None
    """The name from ``Content-Disposition``, unsanitized and untrusted.

    Cleaning it is :func:`~maxicrawler.library.naming.safe_filename`'s job,
    where every stored name already goes. Doing it here as well would put two
    rules on one string.
    """

    @property
    def ok(self) -> bool:
        """Return whether the host answered with content rather than a refusal."""
        return 200 <= self.status < 300


@runtime_checkable
class FileTransport(Protocol):
    """Reads what a host says about a file, and then the file itself.

    The third seam, beside :class:`HttpTransport` and :class:`StreamTransport`,
    and it exists because neither of those can answer the question a provider
    of ordinary files has to ask first: *how big is it, and what is it called?*
    An API transport POSTs JSON and reads a document; a stream transport yields
    bytes and no headers. A file behind a plain URL describes itself in the
    response headers, and something has to read them.

    **Implementations are expected to refuse internal addresses**, on the first
    URL and on every redirect hop. That is not a caller's option here: a
    provider of this kind is handed URLs a crawl found, which is exactly the
    shape of a server-side request forgery, and a transport that guarded only
    when asked would be guarding on the days somebody remembered.
    """

    def head(self, url: str) -> RemoteFile:
        """Return what *url* says about itself, without transferring it.

        A refusing status is **returned, not raised**: 404 is an answer about
        the resource, and an inspection has somewhere to put it. Only a failure
        of the request itself raises.

        Raises:
            AddressRefusedError: *url*, or something it redirected to, points
                inside this machine or this network.
            ProviderTransportError: the request could not be carried out.
        """
        ...

    def open(
        self, url: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE
    ) -> tuple[RemoteFile, Generator[bytes, None, None]]:
        """Return what *url* says about itself, and its content.

        Unlike :meth:`StreamTransport.stream`, the connection is already open
        when this returns — that is the point, because the headers are what
        name the payload and state its size. A caller that abandons the
        transfer must close the generator, which releases the socket.

        A refusing status **raises** here, unlike in :meth:`head`: there is no
        content to hand back and no partial answer worth giving.

        Raises:
            AddressRefusedError: *url*, or something it redirected to, points
                inside this machine or this network.
            ProviderTransportError: the request could not be carried out, the
                host refused it, or it failed while the body was being read.
        """
        ...


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


HeaderSource = Callable[[str], Mapping[str, str]]
"""Answers which extra headers one URL should carry.

A function of the URL rather than a fixed mapping, and that is the whole point:
a header that authorises a request to one host must not be sent to another, and
the only moment that can be decided is when the address is known. A transport
built with one of these asks it again for **every redirect hop**, so a hop off
the host takes the credential with it — which is what urllib does by default,
and the reason this seam is a function.

The transport learns nothing from it beyond header names and values. What makes
a header worth confining, and where the value came from, stays above.
"""


def merged_headers(base: Mapping[str, str], extra: HeaderSource | None, url: str) -> dict[str, str]:
    """Return *base* with whatever *extra* wants on a request to *url*."""
    headers = dict(base)
    if extra is not None:
        headers.update(extra(url))
    return headers


class GuardedRedirectHandler(HTTPRedirectHandler):
    """Follows redirects, but only so far, only to HTTP(S), and only outward.

    Overriding :meth:`redirect_request` alone covers every redirect status:
    the base class aliases 301, 303, 307 and 308 onto its own ``http_error_302``,
    and all of them route through this method. By the time it runs the target
    has been made absolute and requoted, which is the form that will really be
    opened — so it is the one worth judging.

    A ``file:`` target is turned away by the base class before this is reached
    and arrives as an HTTP error rather than as a scheme error. The wording is
    worse and the outcome is the same, which is the trade worth making for a
    handler this small; ``ftp:`` the base class permits, and this refuses.

    One handler serves one request, so nothing here needs locking.
    """

    def __init__(
        self,
        *,
        rule: PrivateNetworkRule,
        max_redirects: int,
        extra_headers: HeaderSource | None = None,
    ) -> None:
        super().__init__()
        self._rule = rule
        self._max_redirects = max_redirects
        self._extra_headers = extra_headers
        self._hops = 0

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        """Vet one hop before letting the base class take it."""
        try:
            require_http_scheme(newurl)
        except ValueError as error:
            message = f"redirect from {safe_target(req.full_url)} to a non-HTTP(S) target"
            raise ProviderTransportError(message) from error
        refuse(self._rule, newurl, what="a redirect")
        self._hops += 1
        if self._hops > self._max_redirects:
            message = f"more than {self._max_redirects} redirects from {safe_target(req.full_url)}"
            raise ProviderTransportError(message)
        following = super().redirect_request(req, fp, code, msg, headers, newurl)
        if following is None:
            return None
        return self._rehead(following, previous=req.full_url, target=newurl)

    def _rehead(self, request: Request, *, previous: str, target: str) -> Request:
        """Re-decide the extra headers for a hop the base class just built.

        The base class copies the original request's headers onto the new one,
        stripping only the content headers. For a header that authorises the
        request that is exactly wrong: a redirect to another host would carry
        the credential to it. So whatever was added for *previous* comes off,
        and whatever belongs to *target* goes on — which for a hop off the host
        is nothing at all.
        """
        if self._extra_headers is None:
            return request
        for name in self._extra_headers(previous):
            request.remove_header(name)
        for name, value in self._extra_headers(target).items():
            request.add_header(name, value)
        return request


def refuse(rule: PrivateNetworkRule, url: str, *, what: str = "the address") -> None:
    """Raise when *rule* will not have *url* reached.

    The provider half of the arrangement described in
    :mod:`maxicrawler.utils.addresses`: the rule answers with a sentence, and
    this turns that sentence into the vocabulary this package fails in. The
    crawl's half turns the same sentence into a recorded skip.
    """
    reason = rule.refusal_for(url)
    if reason is not None:
        raise AddressRefusedError(f"refused {what}: {reason}")


def read_remote_file(response: HTTPResponse | HTTPError) -> RemoteFile:
    """Return what *response* said about the file behind it.

    Takes the error class as readily as the success class on purpose:
    :class:`~urllib.error.HTTPError` *is* a response, and a 404 describes a
    resource as surely as a 200 does.
    """
    headers = response.headers
    return RemoteFile(
        url=response.geturl(),
        status=response.status or 0,
        media_type=_media_type(headers.get("Content-Type")),
        size=_content_length(headers.get("Content-Length")),
        filename=_stated_filename(headers.get("Content-Disposition")),
    )


def _media_type(value: str | None) -> str | None:
    """Return the bare media type of a ``Content-Type`` header."""
    if not value:
        return None
    return value.split(";")[0].strip().lower() or None


def _content_length(value: str | None) -> int | None:
    """Return a stated length, or ``None`` when there was none to read.

    A host that states something unreadable is treated as having stated
    nothing. Guessing from a malformed header would put a number in front of
    somebody that no server ever sent.
    """
    if value is None:
        return None
    try:
        length = int(value.strip())
    except ValueError:
        return None
    return length if length >= 0 else None


def _stated_filename(value: str | None) -> str | None:
    """Return the name in a ``Content-Disposition`` header, if it holds one.

    Parsed by :mod:`email.message`, which is the standard library's own reader
    for this grammar and handles the RFC 2231 ``filename*=UTF-8''…`` form that
    a hand-rolled split does not. ``cgi.parse_header`` used to be the obvious
    tool and is gone as of Python 3.13.
    """
    if not value:
        return None
    message = EmailMessage()
    try:
        message["Content-Disposition"] = value
    except ValueError:
        return None
    name = message.get_filename()
    if not isinstance(name, str):
        return None
    return name.strip() or None


class UrllibFileTransport:
    """A :class:`FileTransport` built on the standard library.

    **Refusing internal addresses is not optional and not configurable away.**
    Passing no *rule* builds the strict one, so a transport somebody wired
    without thinking about it is the safe transport rather than the open one.
    Reaching a home network stays possible — by handing in a rule that says so,
    which is a decision somebody makes rather than one they omit.

    No response-size limit applies, which is
    :class:`UrllibStreamTransport`'s rule and for the same reason: a transfer
    is expected to be large and is written straight to disk rather than
    accumulated. Nothing bounds how much one download may be.
    """

    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float = DEFAULT_TIMEOUT,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        rule: PrivateNetworkRule | None = None,
        extra_headers: HeaderSource | None = None,
    ) -> None:
        if timeout <= 0:
            msg = "timeout must be positive"
            raise ValueError(msg)
        if max_redirects < 0:
            msg = "max_redirects must not be negative"
            raise ValueError(msg)
        self._user_agent = user_agent
        self._timeout = timeout
        self._max_redirects = max_redirects
        self._rule = rule if rule is not None else PrivateNetworkRule()
        self._extra_headers = extra_headers

    @property
    def rule(self) -> PrivateNetworkRule:
        """Return the rule every request of this transport is held to."""
        return self._rule

    def head(self, url: str) -> RemoteFile:
        """Return what *url* says about itself, without transferring it."""
        try:
            with closing(self._request(url, method="HEAD")) as response:
                return read_remote_file(response)
        except HTTPError as error:
            with closing(error):
                if error.status not in NO_METHOD_STATUSES:
                    return read_remote_file(error)
        # The host will not answer HEAD. Ask the way it does answer and read
        # the headers off a body we never pull -- closing the response before
        # the first read costs one set of headers, not one file.
        return self._probe_with_get(url)

    def _probe_with_get(self, url: str) -> RemoteFile:
        """Return the headers of a GET whose body is dropped unread."""
        try:
            with closing(self._request(url, method="GET")) as response:
                return read_remote_file(response)
        except HTTPError as error:
            with closing(error):
                return read_remote_file(error)

    def open(
        self, url: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE
    ) -> tuple[RemoteFile, Generator[bytes, None, None]]:
        """Return what *url* says about itself, and its content."""
        if chunk_size <= 0:
            msg = "chunk_size must be positive"
            raise ValueError(msg)
        try:
            response = self._request(url, method="GET")
        except HTTPError as error:
            with closing(error):
                message = f"HTTP {error.status} from {safe_target(url)}"
                raise ProviderTransportError(message) from error
        remote = read_remote_file(response)
        return remote, self._chunks(response, url=remote.url, chunk_size=chunk_size)

    def _chunks(
        self, response: HTTPResponse, *, url: str, chunk_size: int
    ) -> Generator[bytes, None, None]:
        """Yield the body of an already-open *response*, chunk by chunk."""
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

    def _request(self, url: str, *, method: str) -> HTTPResponse:
        """Open *url*, judging it and every hop it redirects through."""
        try:
            require_http_scheme(url)
        except ValueError as error:
            raise ProviderTransportError(str(error)) from error
        refuse(self._rule, url)
        request = Request(  # noqa: S310 - the scheme is checked above
            url,
            headers=merged_headers(
                {"User-Agent": self._user_agent, "Accept": "*/*"}, self._extra_headers, url
            ),
            method=method,
        )
        opener = build_opener(
            GuardedRedirectHandler(
                rule=self._rule,
                max_redirects=self._max_redirects,
                extra_headers=self._extra_headers,
            )
        )
        try:
            return opener.open(request, timeout=self._timeout)  # type: ignore[no-any-return]
        except (HTTPError, ProviderTransportError):
            # An HTTP status is an answer and the caller decides what it means;
            # a refusal raised inside the redirect handler is already in this
            # package's vocabulary. Neither is a failure to reach the network.
            raise
        except (URLError, TimeoutError, OSError) as error:
            msg = f"request to {safe_target(url)} failed"
            raise ProviderTransportError(msg) from error


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
