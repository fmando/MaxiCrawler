"""Retrieving one document over HTTP.

The fetcher is the only part of the web layer that opens a socket, and it is
deliberately thin: it knows about schemes, headers, redirects, size limits, and
compression, and nothing about what the bytes mean.

Every limit it enforces exists because the page on the other end is not ours:

* the scheme allow-list keeps ``file:``, ``data:``, and ``javascript:`` targets
  away from a socket, on the first request and on every redirect hop;
* the redirect cap makes a loop terminate, and records the chain so a caller
  can see where it went;
* the content type is checked from the headers *before* the body is read, so a
  multi-gigabyte video answered to a page request costs one round trip rather
  than a download;
* the size limit applies to the bytes as they arrive *and* to what a compressed
  response expands to, so a small archive that inflates to gigabytes is refused
  like a large one.

A separate fetcher exists rather than a reuse of
:mod:`maxicrawler.providers.transport` because neither transport there fits: one
posts JSON and returns JSON, the other streams bytes without exposing a status,
the headers, or the URL that finally answered — which are the three things a
crawler needs most. Reaching for them would also make this layer depend on the
provider layer, which it must not.
"""

import zlib
from collections.abc import Callable
from email.message import Message
from http.client import HTTPMessage
from typing import Any, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener

from maxicrawler.utils import require_http_scheme, safe_target
from maxicrawler.web.errors import (
    ContentEncodingError,
    ContentTypeError,
    HttpStatusError,
    ResponseTooLargeError,
    TooManyRedirectsError,
    TransportError,
    UnsupportedSchemeError,
)
from maxicrawler.web.models import FetchedPage

try:  # pragma: no cover - exercised by whichever branch the environment has
    import brotli as _brotli
except ImportError:  # pragma: no cover
    try:
        import brotlicffi as _brotli
    except ImportError:
        _brotli = None

BROTLI: Any | None = _brotli
"""The Brotli decoder, when one is installed; ``None`` otherwise.

Brotli is an optional extra rather than a dependency, and ``Accept-Encoding``
advertises ``br`` only when this is not ``None``. We never ask for an encoding
we cannot read.
"""

_BROTLI_ERRORS: tuple[type[BaseException], ...] = (
    (BROTLI.error,) if BROTLI is not None and hasattr(BROTLI, "error") else ()
)
"""Whatever the installed binding raises for a malformed Brotli stream."""

DEFAULT_TIMEOUT = 30.0
"""Seconds to wait for a response before giving up."""

DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
"""Upper bound on a decoded page, so a hostile reply cannot exhaust memory.

Generous for HTML — the largest pages in the wild are a small fraction of it —
and small enough that refusing is cheap.
"""

DEFAULT_MAX_REDIRECTS = 5
"""How many hops a chain may take before it is treated as a loop."""

MAX_SUPPORTED_REDIRECTS: int = HTTPRedirectHandler.max_redirections
"""The ceiling a configured redirect limit may not exceed.

``urllib`` applies a loop guard of its own after each hop. Staying under it
means the limit that fires is always the configured one, so the error a caller
sees names the number that caller chose.
"""

HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})
"""What a page request is willing to accept."""

ACCEPT_HEADER = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"
"""Stated preference; servers that ignore it are caught by the type check."""

RedirectGuard = Callable[[str], None]
"""Vets one redirect target, raising to refuse it.

A callable rather than a policy object, so this module stays free of
:mod:`maxicrawler.web.policy`: what it needs is something that raises, and what
the rule is remains somebody else's business.
"""

_BROTLI_CHUNK = 64 * 1024
"""How much compressed input is handed to the Brotli decoder at a time.

Brotli's decoder has no output limit of its own, so the input is fed in slices
and the accumulated output is measured between them.
"""


@runtime_checkable
class PageFetcher(Protocol):
    """Retrieves one document and reports what answered."""

    def fetch(self, url: str) -> FetchedPage:
        """Return the document at *url*.

        Implementations follow redirects and report the URL that finally
        answered, because that is what relative links on the page resolve
        against.

        Raises:
            FetchError: the document could not be retrieved, or what came back
                was not what was asked for.
        """
        ...


class BoundedRedirectHandler(HTTPRedirectHandler):
    """Follows redirects, but only so far and only to HTTP(S).

    The standard handler permits ``ftp:`` targets and keeps its limit and its
    chain to itself. Both matter here: a crawler has to be able to say where a
    URL ended up, and a redirect is an instruction from a stranger.

    The scheme is vetted twice, because the standard handler splits the work.
    Its own check runs before :meth:`redirect_request` is ever called and
    rejects a ``file:`` target as a plain HTTP error, which would reach a caller
    as "the server said 302" rather than as what actually happened; so
    :meth:`http_error_302` pre-empts it. By the time
    :meth:`redirect_request` runs, the target has been made absolute and
    requoted, which is the form that will really be opened — so it is checked
    again there, and that is the check the chain and the limit hang off.

    One handler serves one request, so the recorded chain needs no locking.

    A caller may also pass a *guard*, called with every hop before it is taken.
    That is where a rule about the *destination* belongs — a public URL
    answering ``302 Location: http://169.254.169.254/`` is the ordinary shape
    of an SSRF, and a check made only against the URL a crawl started with
    would never see it. The guard is a plain callable that raises, so this
    module keeps knowing nothing about policies.
    """

    def __init__(
        self,
        *,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        guard: RedirectGuard | None = None,
    ) -> None:
        super().__init__()
        self._max_redirects = max_redirects
        self._guard = guard
        self.chain: list[str] = []
        """Every URL the chain passed through, in order."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        """Record and vet one hop before letting the base class take it.

        *newurl* has already been made absolute by the caller, so this is the
        first point at which the real destination is known.
        """
        try:
            require_http_scheme(newurl)
        except ValueError as error:
            message = f"redirect from {safe_target(req.full_url)} to a non-HTTP(S) target"
            raise UnsupportedSchemeError(message) from error
        if self._guard is not None:
            self._guard(newurl)
        if len(self.chain) >= self._max_redirects:
            message = (
                f"more than {self._max_redirects} redirects starting at "
                f"{safe_target(self.chain[0] if self.chain else req.full_url)}"
            )
            raise TooManyRedirectsError(message)
        self.chain.append(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

    def http_error_302(
        self, req: Request, fp: Any, code: int, msg: str, headers: HTTPMessage
    ) -> Any:
        """Vet the announced target before the base class judges its scheme."""
        target = headers.get("location") or headers.get("uri")
        if target is not None:
            destination = urljoin(req.full_url, target)
            try:
                require_http_scheme(destination)
            except ValueError as error:
                message = f"redirect from {safe_target(req.full_url)} to a non-HTTP(S) target"
                raise UnsupportedSchemeError(message) from error
        return super().http_error_302(req, fp, code, msg, headers)

    # The base class aliases the other four statuses onto its *own* method
    # object, so overriding 302 alone would leave 301, 303, 307, and 308 going
    # around this handler entirely.
    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


class UrllibPageFetcher:
    """A :class:`PageFetcher` built on the standard library.

    Using ``urllib`` keeps MaxiCrawler's runtime dependency footprint where it
    is. Connection reuse and HTTP/2 are what a third-party client would add,
    and neither matters for fetching a single page.
    """

    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float = DEFAULT_TIMEOUT,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        accept: frozenset[str] = HTML_MEDIA_TYPES,
        guard: RedirectGuard | None = None,
    ) -> None:
        if timeout <= 0:
            msg = "timeout must be positive"
            raise ValueError(msg)
        if max_response_bytes <= 0:
            msg = "max_response_bytes must be positive"
            raise ValueError(msg)
        if max_redirects < 0:
            msg = "max_redirects must not be negative"
            raise ValueError(msg)
        if max_redirects > MAX_SUPPORTED_REDIRECTS:
            msg = f"max_redirects must not exceed {MAX_SUPPORTED_REDIRECTS}"
            raise ValueError(msg)
        if not accept:
            msg = "accept must name at least one media type"
            raise ValueError(msg)
        self._user_agent = user_agent
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects
        self._accept = accept
        self._guard = guard

    @property
    def accepted_media_types(self) -> frozenset[str]:
        """Return the media types this fetcher will read a body for."""
        return self._accept

    def fetch(self, url: str) -> FetchedPage:
        """Return the document at *url*."""
        try:
            require_http_scheme(url)
        except ValueError as error:
            raise UnsupportedSchemeError(str(error)) from error
        handler = BoundedRedirectHandler(max_redirects=self._max_redirects, guard=self._guard)
        request = Request(  # noqa: S310 - the scheme is checked above
            url,
            headers=self._headers(),
            method="GET",
        )
        opener = build_opener(handler)
        try:
            response = opener.open(request, timeout=self._timeout)
        except HTTPError as error:
            error.close()
            message = f"HTTP {error.code} from {safe_target(url)}"
            raise HttpStatusError(message, status=error.code) from error
        except (URLError, TimeoutError, OSError) as error:
            message = f"request to {safe_target(url)} failed"
            raise TransportError(message) from error
        with response:
            content_type, charset = _describe_content(response.headers)
            self._require_accepted(content_type, url=url)
            self._require_announced_size(response.headers, url=url)
            body = self._read_bounded(response, url=url)
            encoding = response.headers.get("Content-Encoding")
            final_url = str(response.url)
            status = int(response.status)
        return FetchedPage(
            requested_url=url,
            final_url=final_url,
            status=status,
            body=_decompress(body, encoding, limit=self._max_response_bytes, url=url),
            content_type=content_type,
            declared_charset=charset,
            content_encoding=_normalize_encoding(encoding),
            redirects=tuple(handler.chain),
        )

    def _headers(self) -> dict[str, str]:
        """Return the request headers, advertising only what we can decode."""
        encodings = ["gzip", "deflate"]
        if BROTLI is not None:
            encodings.append("br")
        return {
            "User-Agent": self._user_agent,
            "Accept": ACCEPT_HEADER,
            "Accept-Encoding": ", ".join(encodings),
        }

    def _require_accepted(self, content_type: str | None, *, url: str) -> None:
        """Refuse a response whose media type we did not ask for.

        Checked before the body is read, which is the whole point: this is the
        only moment at which a large file can be declined for free.
        """
        if content_type is not None and content_type in self._accept:
            return
        announced = content_type or "no content type"
        message = f"{safe_target(url)} answered with {announced}, not a page"
        raise ContentTypeError(message, content_type=content_type)

    def _require_announced_size(self, headers: Message, *, url: str) -> None:
        """Refuse a response that announces more than we will hold.

        Only an early exit: the header is optional and may lie, so the bound
        that actually protects us is applied while reading.
        """
        announced = headers.get("Content-Length")
        if announced is None:
            return
        try:
            length = int(announced)
        except ValueError:
            return
        if length > self._max_response_bytes:
            message = (
                f"{safe_target(url)} announced {length} bytes, "
                f"more than the {self._max_response_bytes} allowed"
            )
            raise ResponseTooLargeError(message)

    def _read_bounded(self, response: Any, *, url: str) -> bytes:
        """Return the body, refusing to hold more than the limit."""
        try:
            body: bytes = response.read(self._max_response_bytes + 1)
        except (URLError, TimeoutError, OSError) as error:
            message = f"reading {safe_target(url)} was interrupted"
            raise TransportError(message) from error
        if len(body) > self._max_response_bytes:
            message = (
                f"{safe_target(url)} sent more than the {self._max_response_bytes} bytes allowed"
            )
            raise ResponseTooLargeError(message)
        return body


def _describe_content(headers: Message) -> tuple[str | None, str | None]:
    """Return the media type and charset a response announced.

    ``Message.get_content_type`` invents ``text/plain`` when the header is
    absent, which would turn "said nothing" into "said the wrong thing". The
    raw header is consulted first so the two stay distinguishable.
    """
    if headers.get("Content-Type") is None:
        return None, None
    return headers.get_content_type(), headers.get_content_charset()


def _normalize_encoding(encoding: str | None) -> str | None:
    """Return the lowercased content coding, or ``None`` for an absent one."""
    if encoding is None:
        return None
    cleaned = encoding.strip().lower()
    return cleaned or None


def _decompress(body: bytes, encoding: str | None, *, limit: int, url: str) -> bytes:
    """Return *body* decompressed according to *encoding*.

    The limit is applied to the *output*, because that is what ends up in
    memory. A response that expands past it is refused rather than truncated:
    half a page would be parsed as a page.
    """
    coding = _normalize_encoding(encoding)
    if coding is None or coding == "identity":
        return body
    try:
        if coding == "gzip":
            return _inflate(body, wbits=zlib.MAX_WBITS | 16, limit=limit, url=url)
        if coding == "deflate":
            return _inflate_deflate(body, limit=limit, url=url)
        if coding == "br":
            return _unbrotli(body, limit=limit, url=url)
    except zlib.error as error:
        message = f"{safe_target(url)} sent a broken {coding} body"
        raise ContentEncodingError(message) from error
    message = f"{safe_target(url)} used the unsupported content coding {coding}"
    raise ContentEncodingError(message)


def _inflate(body: bytes, *, wbits: int, limit: int, url: str) -> bytes:
    """Return *body* inflated, refusing to produce more than *limit* bytes."""
    decompressor = zlib.decompressobj(wbits)
    out = decompressor.decompress(body, limit + 1)
    if len(out) > limit or decompressor.unconsumed_tail:
        raise _too_large(url, limit)
    out += decompressor.flush()
    if len(out) > limit:
        raise _too_large(url, limit)
    return out


def _inflate_deflate(body: bytes, *, limit: int, url: str) -> bytes:
    """Return a ``deflate`` body inflated, in either framing it may use.

    The header says zlib; a fair number of servers send a raw stream instead,
    so the second framing is tried before the body is called broken.
    """
    try:
        return _inflate(body, wbits=zlib.MAX_WBITS, limit=limit, url=url)
    except zlib.error:
        return _inflate(body, wbits=-zlib.MAX_WBITS, limit=limit, url=url)


def _unbrotli(body: bytes, *, limit: int, url: str) -> bytes:
    """Return a Brotli body decompressed, bounded by *limit*.

    The decoder offers no output limit, so the compressed input is fed in
    slices and the accumulated output is measured between them.
    """
    if BROTLI is None:
        message = f"{safe_target(url)} sent a Brotli body and no decoder is installed"
        raise ContentEncodingError(message)
    decompressor = BROTLI.Decompressor()
    out = bytearray()
    try:
        for start in range(0, len(body), _BROTLI_CHUNK):
            out += bytes(decompressor.process(body[start : start + _BROTLI_CHUNK]))
            if len(out) > limit:
                raise _too_large(url, limit)
    except _BROTLI_ERRORS as error:
        message = f"{safe_target(url)} sent a broken br body"
        raise ContentEncodingError(message) from error
    return bytes(out)


def _too_large(url: str, limit: int) -> ResponseTooLargeError:
    """Return the error for a body that expands past *limit*."""
    message = f"{safe_target(url)} expands to more than the {limit} bytes allowed"
    return ResponseTooLargeError(message)
