"""Tests for the provider that fetches files sitting at a plain URL.

Against the real transport and a real local server, because the two halves
that matter -- what a response says a file is called, and what a redirect does
to that -- only exist where sockets do.
"""

import pytest
from web_server import Site, serve

from maxicrawler.domain import (
    Availability,
    ProviderCapability,
    ResourceKind,
    ResourceRef,
    UrlCategory,
    UrlClassification,
    UrlRecord,
)
from maxicrawler.library.naming import resource_key
from maxicrawler.providers.direct import (
    DIRECT_PROVIDER_NAME,
    DIRECT_PROVIDER_PRIORITY,
    DirectProvider,
    availability_for,
)
from maxicrawler.providers.errors import ProviderTransportError, UnsupportedResourceError
from maxicrawler.providers.mega import MEGA_PROVIDER_PRIORITY
from maxicrawler.providers.protocol import ResourceProvider
from maxicrawler.providers.transport import UrllibFileTransport
from maxicrawler.utils.addresses import PrivateNetworkRule

PAYLOAD = b"\x89PNG" + b"y" * 2000
MEGA_LINK = "https://mega.nz/file/AaBbCcDd#0123456789abcdefghijklmnopqrstuvwxyzABC"


class RecordingSink:
    """A sink that keeps what it was told, so a test can read it back."""

    def __init__(self) -> None:
        self.descriptor: object | None = None
        self.body = bytearray()

    def begin(self, content: object) -> None:
        """Record the announced payload."""
        self.descriptor = content

    def write(self, chunk: bytes) -> None:
        """Append *chunk*."""
        self.body.extend(chunk)


def make_provider(**kwargs: object) -> DirectProvider:
    """Return a provider over a transport that may reach the local server."""
    transport = UrllibFileTransport(
        user_agent="MaxiCrawler/test",
        timeout=5.0,
        rule=PrivateNetworkRule(allow_private=True),
    )
    return DirectProvider(transport, **kwargs)  # type: ignore[arg-type]


def classify(url: str) -> UrlClassification:
    """Return a classification of *url*, as a plugin would produce."""
    return UrlClassification(
        record=UrlRecord(raw_url=url, normalized_url=url),
        category=UrlCategory.GENERIC,
        plugin_name="generic",
    )


def make_site() -> Site:
    """Return a site serving one image."""
    site = Site()
    site.add("/hr/1234.png", body=PAYLOAD, content_type="image/png")
    return site


def reference_to(url: str) -> ResourceRef:
    """Return the reference the provider builds for *url*."""
    return make_provider().reference(classify(url))


# --- what it claims ----------------------------------------------------------


def test_it_satisfies_the_provider_protocol() -> None:
    assert isinstance(make_provider(), ResourceProvider)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/photo.jpg",
        "http://example.test/",
        "https://example.test/page.html",
        MEGA_LINK,
    ],
)
def test_it_claims_every_http_url_including_ones_others_want(url: str) -> None:
    """Claiming broadly is safe because priority, not refusal, decides.

    It genuinely can transfer any of these. Whether it *should* is settled by
    a registry that asks the specialised providers first.
    """
    assert make_provider().supports(classify(url)) is True


@pytest.mark.parametrize("url", ["mailto:a@b.test", "file:///etc/passwd", "not a url", "/relative"])
def test_it_claims_nothing_that_is_not_an_absolute_http_url(url: str) -> None:
    assert make_provider().supports(classify(url)) is False


def test_it_sits_below_every_specialised_provider() -> None:
    """A Mega link must reach the provider that can decrypt it.

    This one would faithfully store the ciphertext, which is the wrong kind of
    success.
    """
    assert DIRECT_PROVIDER_PRIORITY < MEGA_PROVIDER_PRIORITY


def test_it_offers_no_listing() -> None:
    """A page that lists files is a crawl, and there is one of those already."""
    capabilities = make_provider().metadata.capabilities

    assert ProviderCapability.DOWNLOAD in capabilities
    assert ProviderCapability.INSPECT in capabilities
    assert ProviderCapability.LIST not in capabilities


def test_without_a_transport_it_advertises_nothing_rather_than_failing_later() -> None:
    assert DirectProvider().metadata.capabilities == frozenset()


# --- the reference -----------------------------------------------------------


def test_a_reference_needs_no_request() -> None:
    """Pure, so references can be built, stored and compared offline."""
    ref = DirectProvider().reference(classify("https://example.test/a/b.png"))

    assert ref.provider == DIRECT_PROVIDER_NAME
    assert ref.kind is ResourceKind.FILE
    assert ref.url == "https://example.test/a/b.png"


def test_the_host_and_the_path_are_kept_apart() -> None:
    ref = reference_to("https://Example.test/hr/1234.png")

    assert ref.parent_id == "example.test"
    assert ref.resource_id == "/hr/1234.png"


def test_a_query_string_is_part_of_what_is_addressed() -> None:
    """`?id=1` and `?id=2` are two files however alike the paths look."""
    first = reference_to("https://example.test/get?id=1")
    second = reference_to("https://example.test/get?id=2")

    assert first.resource_id != second.resource_id
    assert resource_key(first) != resource_key(second)


def test_the_same_name_on_two_hosts_stays_two_entries() -> None:
    """What keeping the host in the identity is for."""
    first = reference_to("https://a.test/1.jpg")
    second = reference_to("https://b.test/1.jpg")

    assert resource_key(first) != resource_key(second)


def test_the_same_url_always_addresses_the_same_entry() -> None:
    assert resource_key(reference_to("https://a.test/1.jpg")) == resource_key(
        reference_to("https://a.test/1.jpg")
    )


def test_a_library_key_can_be_read_by_a_person() -> None:
    """The reason the path rather than the whole URL is the resource id."""
    assert resource_key(reference_to("https://example.test/hr/1234.png")).startswith("hr1234png")


def test_a_fragment_never_reaches_the_reference() -> None:
    ref = reference_to("https://example.test/doc.pdf#page=3")

    assert ref.url == "https://example.test/doc.pdf"
    assert ref.secret is None


def test_a_url_with_no_path_still_addresses_something() -> None:
    assert reference_to("https://example.test").resource_id == "/"


def test_a_reference_to_something_else_is_refused() -> None:
    with pytest.raises(UnsupportedResourceError, match="not an absolute"):
        DirectProvider().reference(classify("mailto:a@b.test"))


# --- inspecting --------------------------------------------------------------


def test_an_inspection_reports_the_size_and_the_type() -> None:
    with serve(make_site()) as base:
        provider = make_provider()
        inspection = provider.inspect(reference_to(f"{base}/hr/1234.png"))

    assert inspection.availability is Availability.AVAILABLE
    assert inspection.metadata is not None
    assert inspection.metadata.size == len(PAYLOAD)
    assert inspection.metadata.attribute("content_type") == "image/png"


def test_an_inspection_names_the_file_from_its_url() -> None:
    with serve(make_site()) as base:
        inspection = make_provider().inspect(reference_to(f"{base}/hr/1234.png"))

    assert inspection.metadata is not None
    assert inspection.metadata.name == "1234.png"


def test_a_stated_name_beats_the_url() -> None:
    """A host that states a name has said what it wants the file called."""
    site = Site()
    site.add(
        "/download",
        body=PAYLOAD,
        headers=(("Content-Disposition", 'attachment; filename="holiday.png"'),),
    )

    with serve(site) as base:
        inspection = make_provider().inspect(reference_to(f"{base}/download"))

    assert inspection.metadata is not None
    assert inspection.metadata.name == "holiday.png"


def test_a_percent_encoded_url_name_is_read_as_a_name() -> None:
    site = Site()
    site.add("/na%C3%AFve.pdf", body=PAYLOAD)

    with serve(site) as base:
        inspection = make_provider().inspect(reference_to(f"{base}/na%C3%AFve.pdf"))

    assert inspection.metadata is not None
    assert inspection.metadata.name == "naïve.pdf"


def test_a_missing_file_is_reported_rather_than_raised() -> None:
    with serve(make_site()) as base:
        inspection = make_provider().inspect(reference_to(f"{base}/gone.png"))

    assert inspection.availability is Availability.NOT_FOUND
    assert inspection.metadata is None


def test_an_inspection_never_lists_entries() -> None:
    with serve(make_site()) as base:
        inspection = make_provider().inspect(reference_to(f"{base}/hr/1234.png"))

    assert inspection.entries == ()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, Availability.AVAILABLE),
        (204, Availability.AVAILABLE),
        (401, Availability.ACCESS_DENIED),
        (403, Availability.ACCESS_DENIED),
        (404, Availability.NOT_FOUND),
        (410, Availability.NOT_FOUND),
        (429, Availability.RATE_LIMITED),
        (451, Availability.BLOCKED),
    ],
)
def test_a_status_that_says_something_is_read_as_what_it_says(
    status: int, expected: Availability
) -> None:
    assert availability_for(status) is expected


@pytest.mark.parametrize("status", [500, 502, 503, 418])
def test_a_status_that_says_nothing_about_the_file_leaves_it_unknown(status: int) -> None:
    """A broken server has not said the file is gone.

    Recording that it had would be a wrong answer somebody acts on months
    later, when the file is still perfectly there.
    """
    assert availability_for(status) is Availability.UNKNOWN


# --- transferring ------------------------------------------------------------


def test_a_download_streams_the_body_into_the_sink() -> None:
    sink = RecordingSink()

    with serve(make_site()) as base:
        descriptor = make_provider().download(reference_to(f"{base}/hr/1234.png"), sink)

    assert bytes(sink.body) == PAYLOAD
    assert descriptor.name == "1234.png"
    assert descriptor.size == len(PAYLOAD)


def test_the_sink_is_told_what_is_coming_before_the_first_chunk() -> None:
    sink = RecordingSink()

    with serve(make_site()) as base:
        make_provider().download(reference_to(f"{base}/hr/1234.png"), sink)

    assert sink.descriptor is not None


def test_a_transfer_arrives_in_pieces_rather_than_whole() -> None:
    """Memory stays flat however large the file is."""
    written: list[int] = []
    sink = RecordingSink()
    sink.write = lambda chunk: written.append(len(chunk))  # type: ignore[method-assign]

    with serve(make_site()) as base:
        make_provider(chunk_size=512).download(reference_to(f"{base}/hr/1234.png"), sink)

    assert len(written) > 1
    assert max(written) <= 512


def test_the_thing_that_answered_is_the_thing_described() -> None:
    """A redirect can change both the name and the size."""
    site = make_site()
    site.add("/go", status=302, location="/hr/1234.png", body=b"")
    sink = RecordingSink()

    with serve(site) as base:
        descriptor = make_provider().download(reference_to(f"{base}/go"), sink)

    assert descriptor.name == "1234.png"
    assert bytes(sink.body) == PAYLOAD


def test_a_missing_file_fails_a_transfer_rather_than_returning_nothing() -> None:
    with serve(make_site()) as base, pytest.raises(ProviderTransportError, match="404"):
        make_provider().download(reference_to(f"{base}/gone.png"), RecordingSink())


def test_a_sink_that_raises_does_not_leave_the_transfer_open() -> None:
    """A full disk, or a cancelled download. The socket goes either way."""

    class FailingSink(RecordingSink):
        def write(self, chunk: bytes) -> None:
            msg = "no room"
            raise OSError(msg)

    with serve(make_site()) as base, pytest.raises(OSError, match="no room"):
        make_provider(chunk_size=64).download(reference_to(f"{base}/hr/1234.png"), FailingSink())


def test_another_provider_s_reference_is_refused() -> None:
    ref = ResourceRef(
        provider="mega", resource_id="AaBbCcDd", kind=ResourceKind.FILE, url="https://mega.nz/x"
    )

    with pytest.raises(UnsupportedResourceError, match="another provider"):
        make_provider().download(ref, RecordingSink())


def test_a_provider_without_a_transport_says_so_when_asked_to_work() -> None:
    with pytest.raises(UnsupportedResourceError, match="without a transport"):
        DirectProvider().download(reference_to("https://example.test/a.png"), RecordingSink())


def test_a_chunk_size_must_be_usable() -> None:
    with pytest.raises(ValueError, match="chunk_size must be at least 1"):
        DirectProvider(chunk_size=0)
