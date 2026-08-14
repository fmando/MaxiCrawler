"""Tests for the service that reads the library.

Entries are written by hand rather than downloaded, because what is under test
is the reading: which rows match, what order they come in, how a page is cut,
and what happens to an entry that says something unusable. No provider, no
socket, no download.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from maxicrawler.app import (
    DEFAULT_PER_PAGE,
    MAX_PER_PAGE,
    Display,
    LibraryQuery,
    LibraryService,
    LibrarySort,
)
from maxicrawler.app.viewing import MediaKind
from maxicrawler.config import Settings
from maxicrawler.domain import DownloadStatus, ResourceKind, ResourceRef
from maxicrawler.library import METADATA_FILENAME, Library

PAYLOAD = b"payload"


def make_service(tmp_path: Path, **overrides: object) -> tuple[LibraryService, Library]:
    """Return a service over an empty library below *tmp_path*.

    The database goes below *tmp_path* too. The service keeps its listing cache
    there and would otherwise create one in whatever directory the tests were
    started from; where the cache lives is tested in ``test_library_index.py``.
    """
    library = Library(tmp_path / "library")
    settings = Settings(
        library_path=library.root,
        database_path=tmp_path / "maxicrawler.db",
        **overrides,  # type: ignore[arg-type]
    )
    return LibraryService(settings, library=library), library


def write(
    library: Library,
    handle: str,
    *,
    provider: str = "mega",
    name: str | None = "Jump.pdf",
    filename: str | None = "Jump.pdf",
    size: int | None = len(PAYLOAD),
    status: DownloadStatus = DownloadStatus.COMPLETED,
    downloaded_at: datetime | None = datetime(2026, 8, 9, 14, 30, tzinfo=UTC),
    payload: bytes | None = PAYLOAD,
    checksum: str | None = "abc123",
    error: str | None = None,
) -> str:
    """Write one library entry by hand and return its key."""
    ref = ResourceRef(
        provider=provider,
        resource_id=handle,
        kind=ResourceKind.FILE,
        url=f"https://{provider}.nz/file/{handle}",
    )
    entry = library.entry(ref)
    entry.path.mkdir(parents=True, exist_ok=True)
    content = None
    if filename is not None:
        content = {
            "filename": filename,
            "path": f"content/{filename}",
            "size": size if size is not None else 0,
            "checksums": [] if checksum is None else [{"algorithm": "sha256", "value": checksum}],
        }
        if payload is not None:
            stored = entry.content_path(filename)
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_bytes(payload)
    document = {
        "schema": 1,
        "provider": provider,
        "key": entry.key,
        "resource_id": handle,
        "parent_id": None,
        "kind": "file",
        "name": name,
        "source_url": ref.url,
        "source_document": None,
        "status": status.value,
        "discovered_at": None,
        "downloaded_at": None if downloaded_at is None else downloaded_at.isoformat(),
        "attempts": 1,
        "error": error,
        "content": content,
    }
    entry.metadata_path.write_text(json.dumps(document), encoding="utf-8")
    return entry.key


# --- what a listing holds -----------------------------------------------------


def test_an_empty_library_is_an_empty_page(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)

    page = service.browse()

    assert page.items == ()
    assert page.total == 0
    assert page.stored == 0
    assert page.pages == 1
    assert page.providers == ()
    assert page.first == 0
    assert page.last == 0


def test_a_stored_entry_is_described_from_its_own_document(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")

    (item,) = service.browse().items

    assert item.provider == "mega"
    assert item.directory == "mega"
    assert item.key == key
    assert item.name == "Jump.pdf"
    assert item.filename == "Jump.pdf"
    assert item.size == len(PAYLOAD)
    assert item.status is DownloadStatus.COMPLETED
    assert item.checksum == "abc123"
    assert item.source_url == "https://mega.nz/file/AaBbCcDd"
    assert item.path is not None
    assert item.path.read_bytes() == PAYLOAD
    assert item.is_stored is True


def test_a_failed_download_is_listed_too(tmp_path: Path) -> None:
    """It is exactly the row somebody comes to the library looking for."""
    service, library = make_service(tmp_path)
    write(
        library,
        "AaBbCcDd",
        status=DownloadStatus.FAILED,
        filename=None,
        downloaded_at=None,
        error="connection reset",
    )

    (item,) = service.browse().items

    assert item.status is DownloadStatus.FAILED
    assert item.error == "connection reset"
    assert item.path is None
    assert item.size is None
    assert item.is_stored is False


def test_an_unnamed_resource_falls_back_to_its_file_then_its_key(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name=None, filename="content.bin")
    key = write(library, "EeFfGgHh", name=None, filename=None)

    names = {item.name for item in service.browse().items}

    assert names == {"content.bin", key}


def test_a_directory_that_is_not_an_entry_is_not_listed(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    library.initialize()
    (library.root / "mega" / "handmade").mkdir(parents=True)

    assert service.browse().items == ()


def test_one_damaged_entry_does_not_empty_the_listing(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd")
    broken = library.root / "mega" / "broken"
    broken.mkdir(parents=True)
    (broken / METADATA_FILENAME).write_text("{not json", encoding="utf-8")

    page = service.browse()

    assert [item.name for item in page.items] == ["Jump.pdf"]
    assert page.stored == 1


def test_the_providers_present_are_reported(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd")
    write(library, "EeFfGgHh", provider="gofile")

    assert service.browse().providers == ("gofile", "mega")


# --- searching ----------------------------------------------------------------


def test_a_search_matches_the_name_case_insensitively(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="Jump.pdf")
    write(library, "EeFfGgHh", name="ubuntu.iso", filename="ubuntu.iso")

    page = service.browse(LibraryQuery(search="JUMP"))

    assert [item.name for item in page.items] == ["Jump.pdf"]
    assert page.total == 1
    assert page.stored == 2


def test_a_search_matches_the_source_url(tmp_path: Path) -> None:
    """How you find something again when you remember the link, not the name."""
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd")
    write(library, "EeFfGgHh", provider="gofile")

    page = service.browse(LibraryQuery(search="gofile.nz/file"))

    assert [item.directory for item in page.items] == ["gofile"]


def test_a_search_matches_the_stored_file_name(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="something else", filename="report-2026.csv")

    assert service.browse(LibraryQuery(search="2026.csv")).total == 1


def test_a_search_that_matches_nothing_says_so(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd")

    page = service.browse(LibraryQuery(search="nowhere"))

    assert page.items == ()
    assert page.total == 0
    assert page.stored == 1


# --- filtering ----------------------------------------------------------------


def test_a_provider_filter_keeps_one_namespace(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd")
    write(library, "EeFfGgHh", provider="gofile")

    page = service.browse(LibraryQuery(provider="gofile"))

    assert [item.directory for item in page.items] == ["gofile"]
    assert page.providers == ("gofile", "mega")


def test_a_status_filter_keeps_one_verdict(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd")
    write(library, "EeFfGgHh", status=DownloadStatus.FAILED, filename=None)

    page = service.browse(LibraryQuery(status=DownloadStatus.FAILED))

    assert [item.status for item in page.items] == [DownloadStatus.FAILED]


def test_a_kind_filter_keeps_one_category(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="Jump.pdf", filename="Jump.pdf")
    write(library, "EeFfGgHh", name="holiday.jpg", filename="holiday.jpg")
    write(library, "IiJjKkLl", name="release.zip", filename="release.zip")

    page = service.browse(LibraryQuery(kind=MediaKind.IMAGE))

    assert [item.name for item in page.items] == ["holiday.jpg"]
    assert page.kinds == (MediaKind.IMAGE, MediaKind.PDF, MediaKind.ARCHIVE)


def test_a_category_comes_from_the_stored_file_rather_than_the_recorded_name(
    tmp_path: Path,
) -> None:
    """The payload is what the entry actually holds."""
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="the holiday photo", filename="holiday.jpg")

    (item,) = service.browse().items

    assert item.kind is MediaKind.IMAGE


def test_an_entry_with_no_payload_is_still_categorised(tmp_path: Path) -> None:
    """A failure and a refusal are exactly what somebody goes looking for.

    Leaving them in "other" would hide the thumbnails a floor turned away from
    the one filter that would find them.
    """
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="thumb.jpg", filename=None, status=DownloadStatus.FAILED)

    (item,) = service.browse().items

    assert item.kind is MediaKind.IMAGE


def test_a_payload_whose_name_says_nothing_falls_back_to_the_record(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="holiday.jpg", filename="download")

    (item,) = service.browse().items

    assert item.kind is MediaKind.IMAGE


def test_filters_combine(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="keep me")
    write(library, "EeFfGgHh", name="keep me", provider="gofile")

    page = service.browse(LibraryQuery(search="keep", provider="mega"))

    assert page.total == 1


def test_a_kind_narrows_what_another_filter_left(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="holiday.jpg", filename="holiday.jpg")
    write(library, "EeFfGgHh", name="holiday.pdf", filename="holiday.pdf")
    write(library, "IiJjKkLl", name="other.jpg", filename="other.jpg")

    page = service.browse(LibraryQuery(search="holiday", kind=MediaKind.IMAGE))

    assert [item.name for item in page.items] == ["holiday.jpg"]


def test_an_unfiltered_query_says_so() -> None:
    assert LibraryQuery().is_filtered is False
    assert LibraryQuery(search="x").is_filtered is True
    assert LibraryQuery(provider="mega").is_filtered is True
    assert LibraryQuery(kind=MediaKind.IMAGE).is_filtered is True
    assert LibraryQuery(status=DownloadStatus.FAILED).is_filtered is True


# --- ordering -----------------------------------------------------------------


def test_the_newest_download_comes_first_by_default(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="old", downloaded_at=datetime(2026, 1, 1, tzinfo=UTC))
    write(library, "EeFfGgHh", name="new", downloaded_at=datetime(2026, 8, 9, tzinfo=UTC))

    assert [item.name for item in service.browse().items] == ["new", "old"]


@pytest.mark.parametrize("descending", [False, True])
def test_a_time_nobody_recorded_sorts_last_in_either_direction(
    tmp_path: Path, descending: bool
) -> None:
    """ "Unknown" is not an early date, and putting it first would read as one."""
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="dated", downloaded_at=datetime(2026, 1, 1, tzinfo=UTC))
    write(library, "EeFfGgHh", name="undated", downloaded_at=None, status=DownloadStatus.FAILED)

    page = service.browse(LibraryQuery(descending=descending))

    assert [item.name for item in page.items][-1] == "undated"


def test_sorting_by_name_ignores_case(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="beta")
    write(library, "EeFfGgHh", name="Alpha")

    page = service.browse(LibraryQuery(sort=LibrarySort.NAME, descending=False))

    assert [item.name for item in page.items] == ["Alpha", "beta"]


def test_sorting_by_size_puts_an_unknown_size_last(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="small", size=10)
    write(library, "EeFfGgHh", name="large", size=9000)
    write(library, "IiJjKkLl", name="unknown", filename=None, status=DownloadStatus.FAILED)

    page = service.browse(LibraryQuery(sort=LibrarySort.SIZE, descending=True))

    assert [item.name for item in page.items] == ["large", "small", "unknown"]


def test_sorting_by_provider_and_status_works(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", provider="mega")
    write(library, "EeFfGgHh", provider="gofile")

    by_provider = service.browse(LibraryQuery(sort=LibrarySort.PROVIDER, descending=False))
    by_status = service.browse(LibraryQuery(sort=LibrarySort.STATUS))

    assert [item.provider for item in by_provider.items] == ["gofile", "mega"]
    assert len(by_status.items) == 2


def test_two_files_with_the_same_name_keep_their_order(tmp_path: Path) -> None:
    """Otherwise two identical requests answer differently."""
    service, library = make_service(tmp_path)
    for handle in ("AaBbCcDd", "EeFfGgHh", "IiJjKkLl"):
        write(library, handle, name="same.pdf")

    first = [item.key for item in service.browse(LibraryQuery(sort=LibrarySort.NAME)).items]
    second = [item.key for item in service.browse(LibraryQuery(sort=LibrarySort.NAME)).items]

    assert first == second


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("name", LibrarySort.NAME),
        ("size", LibrarySort.SIZE),
        ("downloaded", LibrarySort.DOWNLOADED),
        ("nonsense", LibrarySort.NAME),
        (None, LibrarySort.NAME),
        ("", LibrarySort.NAME),
    ],
)
def test_a_sort_from_a_query_string_is_read_leniently(
    value: str | None, expected: LibrarySort
) -> None:
    """A stale bookmark should not be a refusal."""
    assert LibrarySort.parse(value, default=LibrarySort.NAME) is expected


# --- paging -------------------------------------------------------------------


def test_a_page_reports_where_it_sits(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    for index in range(5):
        write(library, f"Handle{index:02d}", name=f"file-{index}")

    page = service.browse(LibraryQuery(per_page=2, page=2, sort=LibrarySort.NAME, descending=False))

    assert [item.name for item in page.items] == ["file-2", "file-3"]
    assert page.page == 2
    assert page.pages == 3
    assert page.first == 3
    assert page.last == 4
    assert page.has_previous is True
    assert page.has_next is True


def test_a_page_beyond_the_end_lands_on_the_last_one(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd")

    page = service.browse(LibraryQuery(page=99))

    assert page.page == 1
    assert page.pages == 1
    assert len(page.items) == 1


def test_a_page_below_the_first_lands_on_it(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd")

    assert service.browse(LibraryQuery(page=0)).page == 1
    assert service.browse(LibraryQuery(page=-5)).page == 1


def test_a_page_size_is_bounded(tmp_path: Path) -> None:
    """Ten thousand rows is either a mistake or an attempt to stall the server."""
    service, library = make_service(tmp_path)
    for index in range(3):
        write(library, f"Handle{index:02d}")

    assert len(service.browse(LibraryQuery(per_page=10_000)).items) == 3
    assert len(service.browse(LibraryQuery(per_page=0)).items) == 1
    assert MAX_PER_PAGE < 10_000
    assert DEFAULT_PER_PAGE == 50


def test_sorting_happens_before_paging(tmp_path: Path) -> None:
    """The other order would sort a page instead of the library."""
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="c")
    write(library, "EeFfGgHh", name="a")
    write(library, "IiJjKkLl", name="b")

    page = service.browse(LibraryQuery(sort=LibrarySort.NAME, descending=False, per_page=1))

    assert [item.name for item in page.items] == ["a"]


# --- one entry ----------------------------------------------------------------


def test_one_entry_can_be_read_by_provider_and_key(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")

    item = service.item("mega", key)

    assert item is not None
    assert item.name == "Jump.pdf"


@pytest.mark.parametrize(
    ("provider", "key"),
    [("mega", "nothing"), ("nobody", "aabbccdd-0000000000"), ("..", "x"), ("mega", "../secret")],
)
def test_an_entry_that_cannot_be_addressed_is_simply_absent(
    tmp_path: Path, provider: str, key: str
) -> None:
    service, _ = make_service(tmp_path)

    assert service.item(provider, key) is None
    assert service.payload(provider, key) is None


# --- the file behind an entry -------------------------------------------------


def test_a_payload_is_described_with_what_may_be_done_with_it(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd", filename="Jump.pdf")

    payload = service.payload("mega", key)

    assert payload is not None
    assert payload.filename == "Jump.pdf"
    assert payload.size == len(PAYLOAD)
    assert payload.path.is_file()
    assert payload.media.content_type == "application/pdf"
    assert payload.media.display is Display.IFRAME


def test_a_payload_that_was_deleted_is_not_offered(tmp_path: Path) -> None:
    """A library is repairable; a page promising a missing file is not honest."""
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")
    item = service.item("mega", key)
    assert item is not None and item.path is not None
    item.path.unlink()

    assert service.payload("mega", key) is None
    assert service.item("mega", key) is not None


def test_a_record_claiming_no_payload_offers_none(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd", filename=None, status=DownloadStatus.FAILED)

    assert service.payload("mega", key) is None


def test_the_viewer_limit_comes_from_the_configuration(tmp_path: Path) -> None:
    service, library = make_service(tmp_path, max_view_bytes=1)
    key = write(library, "AaBbCcDd", filename="Jump.pdf")

    payload = service.payload("mega", key)

    assert payload is not None
    assert payload.media.can_display is False
    assert "above the viewer's" in (payload.media.reason or "")


def test_an_unviewable_type_is_still_a_payload(tmp_path: Path) -> None:
    """The file is there; only showing it in a browser is refused."""
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd", filename="ubuntu.iso")

    payload = service.payload("mega", key)

    assert payload is not None
    assert payload.media.can_display is False


def test_the_service_names_where_the_library_is(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)

    assert service.library_root == library.root
