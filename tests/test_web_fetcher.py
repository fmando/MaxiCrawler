"""Tests for the page fetcher, exercised against a local HTTP server."""

import zlib

import pytest
from web_server import Site, deflated, gzipped, serve

from maxicrawler.web import (
    ContentEncodingError,
    ContentTypeError,
    FetchedPage,
    HttpStatusError,
    ResponseTooLargeError,
    TooManyRedirectsError,
    TransportError,
    UnsupportedSchemeError,
)
from maxicrawler.web.fetcher import BROTLI, PageFetcher, UrllibPageFetcher

needs_brotli = pytest.mark.skipif(BROTLI is None, reason="no Brotli decoder installed")


def make_fetcher(**kwargs: object) -> UrllibPageFetcher:
    """Return a fetcher with a short timeout so failures stay quick."""
    options: dict[str, object] = {"user_agent": "MaxiCrawler/test", "timeout": 5.0}
    options.update(kwargs)
    return UrllibPageFetcher(**options)  # type: ignore[arg-type]


def test_fetcher_satisfies_the_runtime_protocol() -> None:
    assert isinstance(make_fetcher(), PageFetcher)


def test_a_page_is_returned_with_its_body_and_type() -> None:
    site = Site()
    site.add_html("/", "<html><body>hi</body></html>")

    with serve(site) as base:
        page = make_fetcher().fetch(f"{base}/")

    assert isinstance(page, FetchedPage)
    assert page.status == 200
    assert page.body == b"<html><body>hi</body></html>"
    assert page.content_type == "text/html"
    assert page.declared_charset == "utf-8"


def test_the_configured_user_agent_and_accept_headers_are_sent() -> None:
    site = Site()
    site.add_html("/", "<html></html>")

    with serve(site) as base:
        make_fetcher().fetch(f"{base}/")

    headers = site.requests[0].headers
    assert headers["User-Agent"] == "MaxiCrawler/test"
    assert "text/html" in headers["Accept"]


def test_accept_encoding_advertises_only_what_can_be_decoded() -> None:
    site = Site()
    site.add_html("/", "<html></html>")

    with serve(site) as base:
        make_fetcher().fetch(f"{base}/")

    advertised = {value.strip() for value in site.requests[0].headers["Accept-Encoding"].split(",")}
    assert "gzip" in advertised
    assert "deflate" in advertised
    assert ("br" in advertised) is (BROTLI is not None)


# --- redirects ---------------------------------------------------------------


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_every_redirect_status_is_followed(status: int) -> None:
    site = Site()
    site.add("/start", status=status, location="/end", body=b"", content_type=None)
    site.add_html("/end", "<html>arrived</html>")

    with serve(site) as base:
        page = make_fetcher().fetch(f"{base}/start")

    assert page.body == b"<html>arrived</html>"
    assert page.final_url == f"{base}/end"


def test_the_requested_url_survives_a_redirect() -> None:
    site = Site()
    site.add("/start", status=302, location="/end", body=b"", content_type=None)
    site.add_html("/end", "<html></html>")

    with serve(site) as base:
        page = make_fetcher().fetch(f"{base}/start")

    assert page.requested_url == f"{base}/start"
    assert page.final_url == f"{base}/end"
    assert page.was_redirected is True


def test_the_whole_redirect_chain_is_recorded_in_order() -> None:
    site = Site()
    site.add("/a", status=302, location="/b", body=b"", content_type=None)
    site.add("/b", status=302, location="/c", body=b"", content_type=None)
    site.add_html("/c", "<html></html>")

    with serve(site) as base:
        page = make_fetcher().fetch(f"{base}/a")

    assert page.redirects == (f"{base}/b", f"{base}/c")


def test_a_page_that_does_not_redirect_reports_an_empty_chain() -> None:
    site = Site()
    site.add_html("/", "<html></html>")

    with serve(site) as base:
        page = make_fetcher().fetch(f"{base}/")

    assert page.redirects == ()
    assert page.was_redirected is False


def test_a_redirect_loop_is_capped() -> None:
    site = Site()
    site.add("/loop", status=302, location="/loop", body=b"", content_type=None)

    with serve(site) as base, pytest.raises(TooManyRedirectsError, match="more than 3 redirects"):
        make_fetcher(max_redirects=3).fetch(f"{base}/loop")


def test_a_chain_longer_than_the_limit_is_refused() -> None:
    site = Site()
    site.add("/a", status=302, location="/b", body=b"", content_type=None)
    site.add("/b", status=302, location="/c", body=b"", content_type=None)
    site.add_html("/c", "<html></html>")

    with serve(site) as base, pytest.raises(TooManyRedirectsError):
        make_fetcher(max_redirects=1).fetch(f"{base}/a")


def test_a_redirect_to_ftp_is_refused_although_urllib_would_allow_it() -> None:
    site = Site()
    site.add("/out", status=302, location="ftp://example.test/x", body=b"", content_type=None)

    with serve(site) as base, pytest.raises(UnsupportedSchemeError):
        make_fetcher().fetch(f"{base}/out")


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_to_a_file_url_is_refused_whatever_the_status(status: int) -> None:
    """The base class aliases four statuses onto its own 302 method.

    Overriding 302 alone would let 301, 303, 307, and 308 bypass the check
    entirely, so every one of them is asserted rather than assumed.
    """
    site = Site()
    site.add("/out", status=status, location="file:///etc/passwd", body=b"", content_type=None)

    with serve(site) as base, pytest.raises(UnsupportedSchemeError):
        make_fetcher().fetch(f"{base}/out")


def test_zero_redirects_allowed_means_the_first_hop_is_refused() -> None:
    site = Site()
    site.add("/start", status=302, location="/end", body=b"", content_type=None)
    site.add_html("/end", "<html></html>")

    with serve(site) as base, pytest.raises(TooManyRedirectsError):
        make_fetcher(max_redirects=0).fetch(f"{base}/start")


# --- schemes and transport ---------------------------------------------------


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://example.test/x", "javascript:alert(1)", "/relative"]
)
def test_a_non_http_url_never_reaches_a_socket(url: str) -> None:
    with pytest.raises(UnsupportedSchemeError):
        make_fetcher().fetch(url)


def test_an_unreachable_host_is_a_transport_failure() -> None:
    with pytest.raises(TransportError, match="failed"):
        make_fetcher().fetch("http://127.0.0.1:1/")


def test_an_error_status_is_reported_with_its_code() -> None:
    site = Site()

    with serve(site) as base, pytest.raises(HttpStatusError) as failure:
        make_fetcher().fetch(f"{base}/missing")

    assert failure.value.status == 404


def test_a_server_error_is_reported_with_its_code() -> None:
    site = Site()
    site.add("/boom", status=503, body=b"", content_type="text/html")

    with serve(site) as base, pytest.raises(HttpStatusError) as failure:
        make_fetcher().fetch(f"{base}/boom")

    assert failure.value.status == 503


def test_a_query_string_never_reaches_an_error_message() -> None:
    with pytest.raises(TransportError) as failure:
        make_fetcher().fetch("http://127.0.0.1:1/page?token=SecretValue#SecretKey")

    assert "SecretValue" not in str(failure.value)
    assert "SecretKey" not in str(failure.value)


# --- content type ------------------------------------------------------------


def test_a_non_html_response_is_refused() -> None:
    site = Site()
    site.add("/data.json", body=b"{}", content_type="application/json")

    with serve(site) as base, pytest.raises(ContentTypeError) as failure:
        make_fetcher().fetch(f"{base}/data.json")

    assert failure.value.content_type == "application/json"


def test_a_response_without_a_content_type_is_refused() -> None:
    site = Site()
    site.add("/blob", body=b"...", content_type=None)

    with serve(site) as base, pytest.raises(ContentTypeError) as failure:
        make_fetcher().fetch(f"{base}/blob")

    assert failure.value.content_type is None


def test_xhtml_is_accepted() -> None:
    site = Site()
    site.add("/x", body=b"<html/>", content_type="application/xhtml+xml")

    with serve(site) as base:
        page = make_fetcher().fetch(f"{base}/x")

    assert page.content_type == "application/xhtml+xml"


def test_the_accepted_types_are_configurable() -> None:
    site = Site()
    site.add("/t", body=b"plain", content_type="text/plain")

    with serve(site) as base:
        page = make_fetcher(accept=frozenset({"text/plain"})).fetch(f"{base}/t")

    assert page.body == b"plain"


def test_a_body_is_not_read_when_the_type_is_refused() -> None:
    site = Site()
    site.add("/huge", body=b"x" * 10_000, content_type="video/mp4")

    with serve(site) as base, pytest.raises(ContentTypeError):
        make_fetcher(max_response_bytes=100).fetch(f"{base}/huge")


# --- size limits -------------------------------------------------------------


def test_an_announced_oversize_body_is_refused_before_reading() -> None:
    site = Site()
    site.add_html("/big", "x" * 500)

    with serve(site) as base, pytest.raises(ResponseTooLargeError, match="announced"):
        make_fetcher(max_response_bytes=100).fetch(f"{base}/big")


def test_an_understated_length_cannot_smuggle_an_oversize_body_through() -> None:
    """A short ``Content-Length`` truncates the body rather than defeating the bound.

    The early exit trusts the header, so this pins down that trusting it is
    safe: the transport delivers only what was announced, and the read bound
    still governs everything that is not announced at all.
    """
    site = Site()
    site.add_html("/big", "x" * 500, content_length=10)

    with serve(site) as base:
        page = make_fetcher(max_response_bytes=100).fetch(f"{base}/big")

    assert len(page.body) == 10


def test_an_oversize_body_is_refused_without_a_length_header() -> None:
    site = Site()
    site.add_html("/big", "x" * 500, omit_content_length=True)

    with serve(site) as base, pytest.raises(ResponseTooLargeError):
        make_fetcher(max_response_bytes=100).fetch(f"{base}/big")


def test_a_body_exactly_at_the_limit_is_accepted() -> None:
    site = Site()
    site.add_html("/edge", "x" * 100)

    with serve(site) as base:
        page = make_fetcher(max_response_bytes=100).fetch(f"{base}/edge")

    assert len(page.body) == 100


# --- compression -------------------------------------------------------------


def test_a_gzip_body_is_decompressed() -> None:
    site = Site()
    site.add("/", body=gzipped(b"<html>gz</html>"), content_encoding="gzip")

    with serve(site) as base:
        page = make_fetcher().fetch(f"{base}/")

    assert page.body == b"<html>gz</html>"
    assert page.content_encoding == "gzip"


def test_a_zlib_framed_deflate_body_is_decompressed() -> None:
    site = Site()
    site.add("/", body=deflated(b"<html>zl</html>"), content_encoding="deflate")

    with serve(site) as base:
        page = make_fetcher().fetch(f"{base}/")

    assert page.body == b"<html>zl</html>"


def test_a_raw_deflate_body_is_decompressed() -> None:
    site = Site()
    site.add("/", body=deflated(b"<html>raw</html>", raw=True), content_encoding="deflate")

    with serve(site) as base:
        page = make_fetcher().fetch(f"{base}/")

    assert page.body == b"<html>raw</html>"


def test_an_identity_encoding_is_left_alone() -> None:
    site = Site()
    site.add("/", body=b"<html>plain</html>", content_encoding="identity")

    with serve(site) as base:
        page = make_fetcher().fetch(f"{base}/")

    assert page.body == b"<html>plain</html>"


def test_an_unknown_content_coding_is_reported() -> None:
    site = Site()
    site.add("/", body=b"...", content_encoding="exotic")

    with serve(site) as base, pytest.raises(ContentEncodingError, match="unsupported"):
        make_fetcher().fetch(f"{base}/")


def test_a_broken_gzip_body_is_reported() -> None:
    site = Site()
    site.add("/", body=b"not actually gzip", content_encoding="gzip")

    with serve(site) as base, pytest.raises(ContentEncodingError, match="broken"):
        make_fetcher().fetch(f"{base}/")


def test_a_decompression_bomb_is_refused() -> None:
    bomb = zlib.compress(b"\0" * (4 * 1024 * 1024))
    site = Site()
    site.add("/", body=bomb, content_encoding="deflate")

    with serve(site) as base, pytest.raises(ResponseTooLargeError, match="expands"):
        make_fetcher(max_response_bytes=64 * 1024).fetch(f"{base}/")


def test_a_compressed_body_within_the_limit_is_accepted() -> None:
    payload = b"<html>" + b"a" * 4096 + b"</html>"
    site = Site()
    site.add("/", body=gzipped(payload), content_encoding="gzip")

    with serve(site) as base:
        page = make_fetcher(max_response_bytes=64 * 1024).fetch(f"{base}/")

    assert page.body == payload


@needs_brotli
def test_a_brotli_body_is_decompressed() -> None:
    assert BROTLI is not None
    site = Site()
    site.add("/", body=BROTLI.compress(b"<html>br</html>"), content_encoding="br")

    with serve(site) as base:
        page = make_fetcher().fetch(f"{base}/")

    assert page.body == b"<html>br</html>"


@needs_brotli
def test_a_brotli_bomb_is_refused() -> None:
    assert BROTLI is not None
    site = Site()
    site.add("/", body=BROTLI.compress(b"\0" * (4 * 1024 * 1024)), content_encoding="br")

    with serve(site) as base, pytest.raises(ResponseTooLargeError):
        make_fetcher(max_response_bytes=64 * 1024).fetch(f"{base}/")


@needs_brotli
def test_a_broken_brotli_body_is_reported() -> None:
    site = Site()
    site.add("/", body=b"definitely not brotli", content_encoding="br")

    with serve(site) as base, pytest.raises(ContentEncodingError):
        make_fetcher().fetch(f"{base}/")


# --- construction ------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"timeout": 0}, "timeout must be positive"),
        ({"max_response_bytes": 0}, "max_response_bytes must be positive"),
        ({"max_redirects": -1}, "max_redirects must not be negative"),
        ({"max_redirects": 99}, "max_redirects must not exceed"),
        ({"accept": frozenset()}, "accept must name at least one media type"),
    ],
)
def test_invalid_options_are_refused(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        make_fetcher(**kwargs)


def test_the_accepted_media_types_are_reported() -> None:
    assert "text/html" in make_fetcher().accepted_media_types
