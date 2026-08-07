"""The error hierarchy of the web layer.

Only faults on our side are raised. A page that does not exist, a server that
refuses to answer, and a response we asked for but did not get are all failures
of *this* request; what a resource *is* remains the provider layer's question
and is reported there as a value.

Every message carries a URL reduced by
:func:`~maxicrawler.utils.urls.safe_target`, so no query string or fragment can
reach a log record through a failed fetch.
"""


class CrawlError(RuntimeError):
    """Base class for every failure of the web layer."""


class PolicyRefusedError(CrawlError):
    """Raised when a :class:`~maxicrawler.web.policy.CrawlPolicy` said no.

    The caller named this URL explicitly, so refusing to fetch it is a failure
    of the request. A recursive crawl catches this per URL and records a
    skipped page instead of stopping.
    """


class FetchError(CrawlError):
    """Base class for every failure of a single retrieval."""


class UnsupportedSchemeError(FetchError):
    """Raised for a URL, or a redirect target, that is not HTTP(S).

    This is what keeps ``file:``, ``data:``, and ``javascript:`` targets away
    from a socket, on the first request and on every redirect hop alike.
    """


class TransportError(FetchError):
    """Raised when a request could not be carried out at all.

    Connection failures, DNS failures, and timeouts end up here; the page
    itself may be perfectly healthy.
    """


class HttpStatusError(FetchError):
    """Raised when the server answered with an error status."""

    def __init__(self, message: str, *, status: int) -> None:
        super().__init__(message)
        self.status = status
        """The HTTP status code that was refused."""


class TooManyRedirectsError(FetchError):
    """Raised when a redirect chain exceeded the configured limit."""


class ContentTypeError(FetchError):
    """Raised when the response was not a media type we asked for.

    Checked from the headers, before the body is read, so a multi-gigabyte
    video can be declined without being downloaded.
    """

    def __init__(self, message: str, *, content_type: str | None) -> None:
        super().__init__(message)
        self.content_type = content_type
        """The media type the server announced, when it announced one."""


class ContentEncodingError(FetchError):
    """Raised when a response was compressed in a way we cannot read."""


class ResponseTooLargeError(FetchError):
    """Raised when a body exceeded the configured limit.

    The limit applies to what we would hold in memory, so it is enforced both
    on the bytes as they arrive and on the bytes a compressed response expands
    to — a small archive that inflates to gigabytes is refused just as a large
    one is.
    """
