"""Tests for the Mega resource provider."""

from datetime import UTC, datetime

import pytest
from doubles import make_record
from mega_fixtures import (
    CHILD_FILE_HANDLE,
    CHILD_FOLDER_HANDLE,
    FILE_AES_KEY,
    FILE_HANDLE,
    NESTED_FILE_HANDLE,
    NESTED_SIZE,
    SHARE_HANDLE,
    SHARE_KEY,
    TIMESTAMP,
    UBUNTU_SIZE,
    RecordingTransport,
    encode_base64,
    file_answer,
    file_url,
    folder_answer,
    folder_url,
    pack_file_key,
)

from maxicrawler.domain import (
    Availability,
    ProviderCapability,
    ResourceKind,
    UrlCategory,
    UrlClassification,
)
from maxicrawler.providers import (
    CryptographyCipherBackend,
    ProviderDependencyError,
    ProviderRegistry,
    ResourceProvider,
    Retrier,
    RetryPolicy,
    UnsupportedResourceError,
)
from maxicrawler.providers.mega import MegaApiClient, MegaProvider

FILE_URL = file_url(key=FILE_AES_KEY)
FOLDER_URL = folder_url()


def classify(url: str) -> UrlClassification:
    """Return the classification the Mega plugin would produce for *url*."""
    return UrlClassification(record=make_record(url), category=UrlCategory.FILE, plugin_name="mega")


def provider(
    answers: list[object] | None = None,
    *,
    cipher: object | None = "default",
    max_entries: int = 1000,
) -> tuple[MegaProvider, RecordingTransport]:
    """Return a provider wired to a recording transport."""
    transport = RecordingTransport(answers or [])
    retrier = Retrier(RetryPolicy(max_attempts=2, initial_delay=0.0), sleep=lambda _: None)
    api = MegaApiClient(transport, retrier=retrier)
    backend = CryptographyCipherBackend() if cipher == "default" else cipher
    return (
        MegaProvider(api, cipher=backend, max_entries=max_entries),  # type: ignore[arg-type]
        transport,
    )


def inspect(url: str, answers: list[object], **kwargs: object) -> object:
    """Reference *url* and inspect it against the queued answers."""
    mega, _ = provider(answers, **kwargs)  # type: ignore[arg-type]
    return mega.inspect(mega.reference(classify(url)))


def test_provider_satisfies_the_runtime_protocol() -> None:
    mega, _ = provider()

    assert isinstance(mega, ResourceProvider)


def test_provider_advertises_inspection_and_listing() -> None:
    mega, _ = provider()

    assert mega.metadata.name == "mega"
    assert mega.metadata.label == "Mega"
    assert mega.metadata.supports(ProviderCapability.INSPECT)
    assert mega.metadata.supports(ProviderCapability.LIST)
    assert not mega.metadata.supports(ProviderCapability.DOWNLOAD)


def test_provider_rejects_a_max_entries_below_one() -> None:
    with pytest.raises(ValueError, match="max_entries must be at least 1"):
        MegaProvider(MegaApiClient(RecordingTransport()), max_entries=0)


@pytest.mark.parametrize(
    "url",
    [
        FILE_URL,
        FOLDER_URL,
        "https://mega.nz/file/AaBbCcDd",
        "https://mega.co.nz/#!AaBbCcDd!" + encode_base64(pack_file_key()),
        "https://mega.nz/#F!FolderAA!" + encode_base64(SHARE_KEY),
    ],
)
def test_provider_supports_every_share_link_form(url: str) -> None:
    mega, _ = provider()

    assert mega.supports(classify(url)) is True


@pytest.mark.parametrize("url", ["https://mega.nz/pro", "https://example.test/file/AaBbCcDd"])
def test_provider_declines_a_url_that_is_not_a_share(url: str) -> None:
    mega, _ = provider()

    assert mega.supports(classify(url)) is False


def test_reference_describes_a_file_link() -> None:
    mega, _ = provider()

    ref = mega.reference(classify(FILE_URL))

    assert ref.provider == "mega"
    assert ref.resource_id == FILE_HANDLE
    assert ref.kind is ResourceKind.FILE
    assert ref.has_secret is True
    assert ref.parent_id is None


def test_reference_describes_a_folder_link() -> None:
    mega, _ = provider()

    ref = mega.reference(classify(FOLDER_URL))

    assert ref.resource_id == SHARE_HANDLE
    assert ref.kind is ResourceKind.FOLDER


def test_reference_describes_an_entry_selected_inside_a_folder() -> None:
    mega, _ = provider()
    url = f"{FOLDER_URL}/file/{CHILD_FILE_HANDLE}"

    ref = mega.reference(classify(url))

    assert ref.resource_id == CHILD_FILE_HANDLE
    assert ref.parent_id == SHARE_HANDLE
    assert ref.kind is ResourceKind.FILE
    assert ref.is_contained is True


def test_reference_leaves_a_legacy_entry_kind_open() -> None:
    mega, _ = provider()
    url = f"https://mega.nz/#F!{SHARE_HANDLE}!{encode_base64(SHARE_KEY)}!{CHILD_FILE_HANDLE}"

    ref = mega.reference(classify(url))

    assert ref.kind is ResourceKind.UNKNOWN
    assert ref.parent_id == SHARE_HANDLE


def test_reference_rebuilds_a_canonical_url_without_the_key() -> None:
    mega, _ = provider()

    assert mega.reference(classify(FILE_URL)).url == f"https://mega.nz/file/{FILE_HANDLE}"
    assert mega.reference(classify(FOLDER_URL)).url == f"https://mega.nz/folder/{SHARE_HANDLE}"


def test_reference_rebuilds_a_legacy_url_in_the_modern_form() -> None:
    mega, _ = provider()
    url = "https://mega.co.nz/#!AaBbCcDd!" + encode_base64(pack_file_key())

    assert mega.reference(classify(url)).url == "https://mega.co.nz/file/AaBbCcDd"


def test_reference_records_a_link_without_a_key() -> None:
    mega, _ = provider()

    ref = mega.reference(classify("https://mega.nz/file/AaBbCcDd"))

    assert ref.has_secret is False
    assert ref.secret is None


def test_reference_rejects_a_url_that_is_not_a_share() -> None:
    mega, _ = provider()

    with pytest.raises(UnsupportedResourceError, match="not a Mega share link"):
        mega.reference(classify("https://example.test/file/AaBbCcDd"))


def test_reference_error_does_not_repeat_the_fragment() -> None:
    mega, _ = provider()

    with pytest.raises(UnsupportedResourceError) as failure:
        mega.reference(classify("https://example.test/x#SuperSecretKeyMaterial"))

    assert "SuperSecretKeyMaterial" not in str(failure.value)


def test_inspecting_a_file_reports_name_size_and_availability() -> None:
    result = inspect(FILE_URL, [[file_answer()]])

    assert result.availability is Availability.AVAILABLE  # type: ignore[attr-defined]
    assert result.metadata.name == "ubuntu.iso"  # type: ignore[attr-defined]
    assert result.metadata.size == UBUNTU_SIZE  # type: ignore[attr-defined]
    assert result.kind is ResourceKind.FILE  # type: ignore[attr-defined]
    assert result.names_available is True  # type: ignore[attr-defined]


def test_inspecting_a_file_makes_exactly_one_request() -> None:
    mega, transport = provider([[file_answer()]])

    mega.inspect(mega.reference(classify(FILE_URL)))

    assert len(transport.calls) == 1


def test_inspecting_a_file_without_a_key_still_reports_the_size() -> None:
    result = inspect("https://mega.nz/file/AaBbCcDd", [[file_answer()]])

    assert result.metadata.size == UBUNTU_SIZE  # type: ignore[attr-defined]
    assert result.metadata.name is None  # type: ignore[attr-defined]
    assert result.names_available is False  # type: ignore[attr-defined]


def test_inspecting_a_file_with_a_wrong_key_reports_no_name() -> None:
    url = file_url(key=SHARE_KEY)

    result = inspect(url, [[file_answer()]])

    assert result.metadata.name is None  # type: ignore[attr-defined]
    assert result.names_available is False  # type: ignore[attr-defined]
    assert result.availability is Availability.AVAILABLE  # type: ignore[attr-defined]


def test_inspecting_a_file_tolerates_a_missing_attribute_block() -> None:
    result = inspect(FILE_URL, [[{"s": 42}]])

    assert result.metadata.size == 42  # type: ignore[attr-defined]
    assert result.metadata.name is None  # type: ignore[attr-defined]


def test_a_key_without_the_optional_backend_is_reported() -> None:
    mega, _ = provider([[file_answer()]], cipher=None)

    with pytest.raises(ProviderDependencyError, match="cryptography"):
        mega.inspect(mega.reference(classify(FILE_URL)))


def test_a_link_without_a_key_needs_no_optional_backend() -> None:
    mega, _ = provider([[file_answer()]], cipher=None)

    result = mega.inspect(mega.reference(classify("https://mega.nz/file/AaBbCcDd")))

    assert result.availability is Availability.AVAILABLE
    assert result.names_available is False


def test_inspecting_a_folder_lists_its_entries() -> None:
    result = inspect(FOLDER_URL, [[folder_answer()]])

    names = [entry.metadata.name for entry in result.entries]  # type: ignore[attr-defined]
    assert result.metadata.name == "Ubuntu Releases"  # type: ignore[attr-defined]
    assert names == ["archive", "checksums.txt", "ubuntu.iso"]
    assert result.file_count == 2  # type: ignore[attr-defined]
    assert result.folder_count == 1  # type: ignore[attr-defined]


def test_inspecting_a_folder_sums_the_contained_files() -> None:
    result = inspect(FOLDER_URL, [[folder_answer()]])

    assert result.total_size == UBUNTU_SIZE + NESTED_SIZE  # type: ignore[attr-defined]


def test_inspecting_a_folder_reads_sizes_and_timestamps() -> None:
    result = inspect(FOLDER_URL, [[folder_answer()]])

    entries = {e.metadata.name: e.metadata for e in result.entries}  # type: ignore[attr-defined]
    assert entries["ubuntu.iso"].size == UBUNTU_SIZE
    assert entries["ubuntu.iso"].modified_at == datetime.fromtimestamp(TIMESTAMP, tz=UTC)
    assert entries["archive"].kind is ResourceKind.FOLDER


def test_inspecting_a_folder_makes_exactly_one_request() -> None:
    mega, transport = provider([[folder_answer()]])

    mega.inspect(mega.reference(classify(FOLDER_URL)))

    assert len(transport.calls) == 1


def test_folder_entries_carry_a_reference_of_their_own() -> None:
    result = inspect(FOLDER_URL, [[folder_answer()]])

    entry = next(e for e in result.entries if e.metadata.name == "ubuntu.iso")  # type: ignore
    assert entry.ref.resource_id == CHILD_FILE_HANDLE
    assert entry.ref.parent_id == SHARE_HANDLE
    assert entry.ref.kind is ResourceKind.FILE


def test_inspecting_a_folder_without_a_key_still_reports_the_structure() -> None:
    result = inspect(folder_url(key=None), [[folder_answer()]])

    assert result.names_available is False  # type: ignore[attr-defined]
    assert result.file_count == 2  # type: ignore[attr-defined]
    assert result.total_size == UBUNTU_SIZE + NESTED_SIZE  # type: ignore[attr-defined]
    assert all(e.metadata.name is None for e in result.entries)  # type: ignore[attr-defined]


def test_inspecting_a_folder_with_a_wrong_key_reports_names_as_unreadable() -> None:
    result = inspect(folder_url(key=FILE_AES_KEY), [[folder_answer()]])

    assert result.names_available is False  # type: ignore[attr-defined]
    assert result.file_count == 2  # type: ignore[attr-defined]


def test_folder_entries_are_capped_and_the_cut_is_reported() -> None:
    result = inspect(FOLDER_URL, [[folder_answer()]], max_entries=2)

    assert len(result.entries) == 2  # type: ignore[attr-defined]
    assert result.truncated is True  # type: ignore[attr-defined]


def test_an_uncut_listing_is_not_reported_as_truncated() -> None:
    result = inspect(FOLDER_URL, [[folder_answer()]])

    assert result.truncated is False  # type: ignore[attr-defined]


def test_inspecting_an_entry_inside_a_folder_describes_that_entry() -> None:
    url = f"{FOLDER_URL}/file/{CHILD_FILE_HANDLE}"

    result = inspect(url, [[folder_answer()]])

    assert result.metadata.name == "ubuntu.iso"  # type: ignore[attr-defined]
    assert result.metadata.size == UBUNTU_SIZE  # type: ignore[attr-defined]
    assert result.entries == ()  # type: ignore[attr-defined]


def test_inspecting_a_sub_folder_lists_only_its_own_entries() -> None:
    url = f"{FOLDER_URL}/folder/{CHILD_FOLDER_HANDLE}"

    result = inspect(url, [[folder_answer()]])

    names = [entry.metadata.name for entry in result.entries]  # type: ignore[attr-defined]
    assert result.metadata.name == "archive"  # type: ignore[attr-defined]
    assert names == ["checksums.txt"]


def test_inspecting_a_legacy_entry_resolves_its_kind_from_the_listing() -> None:
    url = f"https://mega.nz/#F!{SHARE_HANDLE}!{encode_base64(SHARE_KEY)}!{NESTED_FILE_HANDLE}"

    result = inspect(url, [[folder_answer()]])

    assert result.kind is ResourceKind.FILE  # type: ignore[attr-defined]
    assert result.metadata.name == "checksums.txt"  # type: ignore[attr-defined]


def test_inspecting_a_missing_entry_reports_it_as_gone() -> None:
    url = f"{FOLDER_URL}/file/ZzZzZzZz"

    result = inspect(url, [[folder_answer()]])

    assert result.availability is Availability.NOT_FOUND  # type: ignore[attr-defined]
    assert result.metadata is None  # type: ignore[attr-defined]


def test_inspecting_an_empty_listing_reports_the_share_as_gone() -> None:
    result = inspect(FOLDER_URL, [[{"f": []}]])

    assert result.availability is Availability.NOT_FOUND  # type: ignore[attr-defined]


def test_a_listing_without_a_marked_root_uses_the_parentless_node() -> None:
    nodes = [
        {"h": "Orphan01", "t": 1},
        {"h": "ChildA01", "p": "Orphan01", "t": 0, "s": 10},
    ]

    result = inspect(folder_url("Orphan01", key=None), [[{"f": nodes}]])

    assert result.availability is Availability.AVAILABLE  # type: ignore[attr-defined]
    assert result.file_count == 1  # type: ignore[attr-defined]


def test_a_listing_containing_a_cycle_does_not_loop() -> None:
    nodes = [
        {"h": SHARE_HANDLE, "t": 2},
        {"h": "LoopAAA1", "p": "LoopAAA2", "t": 1},
        {"h": "LoopAAA2", "p": "LoopAAA1", "t": 1},
        {"h": "ChildA01", "p": SHARE_HANDLE, "t": 0, "s": 10},
    ]

    result = inspect(folder_url(key=None), [[{"f": nodes}]])

    assert result.file_count == 1  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("code", "availability"),
    [
        (-9, Availability.NOT_FOUND),
        (-11, Availability.ACCESS_DENIED),
        (-16, Availability.BLOCKED),
        (-17, Availability.QUOTA_EXCEEDED),
        (-2, Availability.ACCESS_DENIED),
        (-8, Availability.ACCESS_DENIED),
        (-14, Availability.ACCESS_DENIED),
        (-1, Availability.UNKNOWN),
        (-18, Availability.UNKNOWN),
    ],
)
def test_an_api_error_becomes_an_availability(code: int, availability: Availability) -> None:
    result = inspect(FILE_URL, [[code]])

    assert result.availability is availability  # type: ignore[attr-defined]
    assert result.metadata is None  # type: ignore[attr-defined]


def test_an_exhausted_deferral_is_reported_as_rate_limited() -> None:
    result = inspect(FILE_URL, [[-3], [-3]])

    assert result.availability is Availability.RATE_LIMITED  # type: ignore[attr-defined]
    assert result.availability.is_determined is False  # type: ignore[attr-defined]


def test_inspecting_a_reference_of_another_provider_is_rejected() -> None:
    mega, _ = provider()
    other = ProviderRegistry()
    assert len(other) == 0
    ref = mega.reference(classify(FILE_URL))
    foreign = type(ref)(
        provider="pixeldrain",
        resource_id=ref.resource_id,
        kind=ref.kind,
        url=ref.url,
    )

    with pytest.raises(UnsupportedResourceError, match="another provider"):
        mega.inspect(foreign)
