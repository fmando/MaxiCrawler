"""Tests for the service that reads the library.

Entries are written by hand rather than downloaded, because what is under test
is the reading: which rows match, what order they come in, how a page is cut,
and what happens to an entry that says something unusable. No provider, no
socket, no download.
"""

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from maxicrawler.app import (
    DEFAULT_PER_PAGE,
    MAX_PER_PAGE,
    PREVIEW_EXCERPT_BYTES,
    PREVIEW_EXCERPT_LINES,
    Display,
    LibraryQuery,
    LibraryService,
    LibrarySort,
    PreviewShape,
    StateResolver,
    parse_verdict,
)
from maxicrawler.app.viewing import MediaKind
from maxicrawler.config import Settings
from maxicrawler.domain import DownloadStatus, ResourceKind, ResourceRef, ReviewVerdict
from maxicrawler.library import METADATA_FILENAME, Library

PAYLOAD = b"payload"


def make_service(
    tmp_path: Path,
    *,
    queued: StateResolver | None = None,
    **overrides: object,
) -> tuple[LibraryService, Library]:
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
    return LibraryService(settings, library=library, queued=queued), library


def waiting_for(*urls: str) -> StateResolver:
    """Return a queue resolver that claims *urls* are in the line.

    The shape the web application hands in, standing in for
    ``TransferQueue.pending``: asked in bulk, answering with the URLs it was
    given rather than with its own copies of them.
    """
    wanted = frozenset(urls)
    return lambda asked: frozenset(url for url in asked if url in wanted)


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
    source_url: str | None = None,
) -> str:
    """Write one library entry by hand and return its key.

    *source_url* overrides where the record says it came from, which is how two
    entries end up recorded under one link: a share naming a folder is stored as
    one entry per file inside it, all under the container's own URL.
    """
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
        "source_url": ref.url if source_url is None else source_url,
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

    assert [facet.value for facet in service.browse().providers] == ["gofile", "mega"]


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
    assert [facet.value for facet in page.providers] == ["gofile", "mega"]


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
    assert [facet.value for facet in page.kinds] == ["image", "pdf", "archive"]


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


def test_a_lower_bound_keeps_what_is_at_least_that_large(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="small", size=500)
    write(library, "EeFfGgHh", name="exact", size=1000)
    write(library, "IiJjKkLl", name="large", size=5000)

    page = service.browse(LibraryQuery(min_size=1000))

    assert sorted(item.name for item in page.items) == ["exact", "large"]


def test_an_upper_bound_keeps_what_is_at_most_that_large(tmp_path: Path) -> None:
    """Inclusive at the top, so the offered bands are a partition."""
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="small", size=500)
    write(library, "EeFfGgHh", name="exact", size=1000)
    write(library, "IiJjKkLl", name="large", size=5000)

    page = service.browse(LibraryQuery(max_size=1000))

    assert sorted(item.name for item in page.items) == ["exact", "small"]


def test_a_band_is_both_bounds_at_once(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="small", size=500)
    write(library, "EeFfGgHh", name="middle", size=5000)
    write(library, "IiJjKkLl", name="large", size=50_000)

    page = service.browse(LibraryQuery(min_size=1000, max_size=10_000))

    assert [item.name for item in page.items] == ["middle"]


def test_a_size_nobody_recorded_satisfies_no_bound(tmp_path: Path) -> None:
    """Counted as small it would sit under "under 1 MB", as large under "over
    100 MB", and as both it would be in two bands at once."""
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="unmeasured", filename=None, status=DownloadStatus.FAILED)

    assert service.browse(LibraryQuery(min_size=1)).total == 0
    assert service.browse(LibraryQuery(max_size=10**12)).total == 0
    assert service.browse().total == 1


def test_the_facets_carry_how_many_of_each_there_are(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="a.jpg", filename="a.jpg")
    write(library, "EeFfGgHh", name="b.jpg", filename="b.jpg")
    write(library, "IiJjKkLl", name="c.pdf", filename="c.pdf", provider="gofile")

    page = service.browse()

    assert [(facet.value, facet.count) for facet in page.kinds] == [("image", 2), ("pdf", 1)]
    assert [(facet.value, facet.count) for facet in page.providers] == [
        ("gofile", 1),
        ("mega", 2),
    ]


def test_the_facet_counts_are_over_the_library_rather_than_the_matches(
    tmp_path: Path,
) -> None:
    """The same rule the report's chips follow, and the cost of it stated.

    A chip can therefore say two and answer with one row once a search is on.
    What it buys is that choosing a filter never removes the chip you would use
    to choose a different one.
    """
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="a.jpg", filename="a.jpg")
    write(library, "EeFfGgHh", name="b.jpg", filename="b.jpg")

    page = service.browse(LibraryQuery(search="a.jpg"))

    assert page.total == 1
    assert [(facet.value, facet.count) for facet in page.kinds] == [("image", 2)]


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
    assert LibraryQuery(queued=True).is_filtered is True


# --- what the queue is doing to it --------------------------------------------


def test_an_entry_the_queue_is_working_on_is_marked(tmp_path: Path) -> None:
    """The one fact that is not on disk, put beside the ones that are."""
    service, library = make_service(tmp_path, queued=waiting_for("https://mega.nz/file/AaBbCcDd"))
    write(library, "AaBbCcDd", name="again.pdf", status=DownloadStatus.FAILED)
    write(library, "EeFfGgHh", name="done.pdf")

    marked = {item.name: item.queued for item in service.browse().items}

    assert marked == {"again.pdf": True, "done.pdf": False}


def test_nothing_is_marked_without_a_queue_to_ask(tmp_path: Path) -> None:
    """The command line builds the service this way, and must not be told lies."""
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd")

    page = service.browse()

    assert [item.queued for item in page.items] == [False]
    assert page.queued is None


def test_a_service_with_a_queue_counts_none_rather_than_saying_nothing(
    tmp_path: Path,
) -> None:
    """Zero and "nobody can answer" are different answers, and only one is a chip."""
    service, library = make_service(tmp_path, queued=waiting_for())
    write(library, "AaBbCcDd")

    assert service.browse().queued == 0


def test_the_queued_count_covers_the_whole_library(tmp_path: Path) -> None:
    """The same rule the other facet counts follow: a chip counts the library."""
    service, library = make_service(
        tmp_path,
        queued=waiting_for("https://mega.nz/file/AaBbCcDd", "https://mega.nz/file/EeFfGgHh"),
    )
    write(library, "AaBbCcDd", name="one.pdf")
    write(library, "EeFfGgHh", name="two.pdf")
    write(library, "IiJjKkLl", name="three.pdf")

    page = service.browse(LibraryQuery(search="one"))

    assert page.total == 1
    assert page.queued == 2


def test_only_what_is_queued(tmp_path: Path) -> None:
    service, library = make_service(tmp_path, queued=waiting_for("https://mega.nz/file/EeFfGgHh"))
    write(library, "AaBbCcDd", name="done.pdf")
    write(library, "EeFfGgHh", name="again.pdf")

    page = service.browse(LibraryQuery(queued=True))

    assert [item.name for item in page.items] == ["again.pdf"]


def test_the_queue_filter_combines_with_the_others(tmp_path: Path) -> None:
    service, library = make_service(
        tmp_path,
        queued=waiting_for("https://mega.nz/file/AaBbCcDd", "https://mega.nz/file/EeFfGgHh"),
    )
    write(library, "AaBbCcDd", name="holiday.jpg", filename="holiday.jpg")
    write(library, "EeFfGgHh", name="holiday.pdf", filename="holiday.pdf")

    page = service.browse(LibraryQuery(queued=True, kind=MediaKind.IMAGE))

    assert [item.name for item in page.items] == ["holiday.jpg"]


def test_asking_for_queued_without_a_queue_answers_with_nothing(tmp_path: Path) -> None:
    """A typed URL, on a client that has no queue: honestly empty, never everything."""
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd")

    assert service.browse(LibraryQuery(queued=True)).items == ()


def test_one_entry_knows_it_is_queued_too(tmp_path: Path) -> None:
    """The detail page must not read "failed" while the retry is running."""
    service, library = make_service(tmp_path, queued=waiting_for("https://mega.nz/file/AaBbCcDd"))
    key = write(library, "AaBbCcDd", status=DownloadStatus.FAILED)

    item = service.item("mega", key)

    assert item is not None
    assert item.queued is True


def test_the_queue_is_asked_once_for_a_listing(tmp_path: Path) -> None:
    """One question over every URL, not one question per row."""
    asked: list[tuple[str, ...]] = []

    def resolver(urls: Iterable[str]) -> frozenset[str]:
        asked.append(tuple(sorted(urls)))
        return frozenset()

    service, library = make_service(tmp_path, queued=resolver)
    write(library, "AaBbCcDd")
    write(library, "EeFfGgHh")

    service.browse()

    assert len(asked) == 1
    assert len(asked[0]) == 2


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


def test_everything_can_be_asked_for_at_once(tmp_path: Path) -> None:
    """Past the page ceiling, which is what a maintenance pass needs.

    The ceiling stays where it is for :meth:`browse`, and means what it means
    there: a request over HTTP for ten thousand rows is a mistake or an attack.
    A script told to go over the library is neither.
    """
    service, library = make_service(tmp_path)
    for index in range(MAX_PER_PAGE + 5):
        write(library, f"Handle{index:04d}")

    assert len(service.every()) == MAX_PER_PAGE + 5


def test_everything_is_the_pages_put_back_together(tmp_path: Path) -> None:
    """Same reading, same filtering, same order — only the cut is missing.

    Worth pinning down, because the cheap way to get the whole answer used to be
    to ask for page after page, and anything the two disagreed about would be a
    difference between what a person sees and what a pass over the library acts
    on.
    """
    service, library = make_service(tmp_path)
    for index in range(7):
        write(library, f"Handle{index:02d}", name=f"name-{index}")
    query = LibraryQuery(sort=LibrarySort.NAME, descending=False, per_page=2)

    paged: list[str] = []
    for number in range(1, service.browse(query).pages + 1):
        page = service.browse(
            LibraryQuery(sort=LibrarySort.NAME, descending=False, per_page=2, page=number)
        )
        paged.extend(item.key for item in page.items)

    assert [item.key for item in service.every(query)] == paged


def test_everything_answers_the_query_it_is_given(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="keep me")
    write(library, "EeFfGgHh", name="not this one")

    found = service.every(LibraryQuery(search="keep"))

    assert [item.name for item in found] == ["keep me"]


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


# --- what a tile shows in place of the file -----------------------------------


def test_a_small_picture_is_shown_as_itself(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd", name="holiday.jpg", filename="holiday.jpg", size=200_000)
    item = service.item("mega", key)
    assert item is not None

    preview = service.preview(item)

    assert preview.shape is PreviewShape.IMAGE
    assert preview.kind is MediaKind.IMAGE
    assert preview.excerpt == ""


def test_a_large_picture_is_a_symbol_and_never_the_original(tmp_path: Path) -> None:
    """Sixty originals is what the limit exists to keep off one page."""
    service, library = make_service(tmp_path, preview_inline_bytes=1_000_000)
    key = write(library, "AaBbCcDd", name="raw.jpg", filename="raw.jpg", size=4_000_000)
    item = service.item("mega", key)
    assert item is not None

    assert service.preview(item).shape is PreviewShape.SYMBOL


def test_the_limit_is_inclusive_at_the_top(tmp_path: Path) -> None:
    service, library = make_service(tmp_path, preview_inline_bytes=1_000_000)
    key = write(library, "AaBbCcDd", name="exact.jpg", filename="exact.jpg", size=1_000_000)
    item = service.item("mega", key)
    assert item is not None

    assert service.preview(item).shape is PreviewShape.IMAGE


def test_zero_switches_inline_pictures_off_altogether(tmp_path: Path) -> None:
    service, library = make_service(tmp_path, preview_inline_bytes=0)
    key = write(library, "AaBbCcDd", name="holiday.jpg", filename="holiday.jpg", size=1000)
    item = service.item("mega", key)
    assert item is not None

    assert service.preview(item).shape is PreviewShape.SYMBOL


def test_a_picture_no_browser_is_handed_stays_a_symbol(tmp_path: Path) -> None:
    """`.tif` is an image to a filter and is not on the viewer's allow-list."""
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd", name="scan.tif", filename="scan.tif", size=1000)
    item = service.item("mega", key)
    assert item is not None and item.kind is MediaKind.IMAGE

    assert service.preview(item).shape is PreviewShape.SYMBOL


def test_a_text_file_shows_its_first_lines(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(
        library,
        "AaBbCcDd",
        name="notes.txt",
        filename="notes.txt",
        payload=b"first line\nsecond line\n",
    )
    item = service.item("mega", key)
    assert item is not None

    preview = service.preview(item)

    assert preview.shape is PreviewShape.EXCERPT
    assert preview.excerpt == "first line\nsecond line"


def test_an_excerpt_is_bounded_by_lines(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(
        library,
        "AaBbCcDd",
        name="notes.txt",
        filename="notes.txt",
        payload=b"\n".join(f"line {number}".encode() for number in range(100)),
    )
    item = service.item("mega", key)
    assert item is not None

    excerpt = service.preview(item).excerpt

    assert len(excerpt.splitlines()) == PREVIEW_EXCERPT_LINES
    assert excerpt.startswith("line 0")


def test_an_excerpt_is_bounded_by_bytes_as_well(tmp_path: Path) -> None:
    """One line a megabyte long is still one line; only the read stops it."""
    service, library = make_service(tmp_path)
    key = write(
        library,
        "AaBbCcDd",
        name="one.txt",
        filename="one.txt",
        payload=b"x" * 1_000_000,
    )
    item = service.item("mega", key)
    assert item is not None

    assert len(service.preview(item).excerpt) <= PREVIEW_EXCERPT_BYTES


def test_an_empty_text_file_falls_back_to_its_symbol(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd", name="empty.txt", filename="empty.txt", payload=b"")
    item = service.item("mega", key)
    assert item is not None

    assert service.preview(item).shape is PreviewShape.SYMBOL


def test_a_record_whose_file_is_gone_shows_a_symbol(tmp_path: Path) -> None:
    """A broken picture in a tile would be the page's own fault, not the library's."""
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd", name="holiday.jpg", filename="holiday.jpg", size=1000)
    item = service.item("mega", key)
    assert item is not None and item.path is not None
    item.path.unlink()

    assert service.preview(item).shape is PreviewShape.SYMBOL


def test_an_entry_with_no_payload_shows_a_symbol(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(
        library,
        "AaBbCcDd",
        name="holiday.jpg",
        filename=None,
        status=DownloadStatus.FAILED,
    )
    item = service.item("mega", key)
    assert item is not None

    preview = service.preview(item)

    assert preview.shape is PreviewShape.SYMBOL
    assert preview.kind is MediaKind.IMAGE


def test_previews_answer_in_the_order_they_were_asked(tmp_path: Path) -> None:
    """The client zips them against the rows, so the order is the whole contract."""
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="a.jpg", filename="a.jpg", size=1000)
    write(library, "EeFfGgHh", name="b.zip", filename="b.zip", size=1000)
    items = service.browse(LibraryQuery(sort=LibrarySort.NAME, descending=False)).items

    previews = service.previews(items)

    assert [preview.kind for preview in previews] == [MediaKind.IMAGE, MediaKind.ARCHIVE]
    assert [preview.shape for preview in previews] == [
        PreviewShape.IMAGE,
        PreviewShape.SYMBOL,
    ]


# --- saying what you think of a file ------------------------------------------


def test_a_verdict_is_written_and_read_back(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")

    item = service.review("mega", key, verdict=ReviewVerdict.KEPT)

    assert item is not None
    assert item.verdict is ReviewVerdict.KEPT
    assert service.item("mega", key).verdict is ReviewVerdict.KEPT  # type: ignore[union-attr]


def test_judging_leaves_every_other_field_alone(tmp_path: Path) -> None:
    """One writer per set of members; that is the whole of ADR-028 here."""
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")
    before = service.item("mega", key)
    assert before is not None

    service.review("mega", key, verdict=ReviewVerdict.IGNORED)
    after = service.item("mega", key)

    assert after is not None
    assert after.status is before.status
    assert after.size == before.size
    assert after.checksum == before.checksum
    assert after.source_url == before.source_url
    assert after.downloaded_at == before.downloaded_at


def test_an_unknown_member_survives_being_judged(tmp_path: Path) -> None:
    """ADR-013 promises a round trip; a review is a round trip like any other."""
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")
    entry = library.entry_at("mega", key)
    assert entry is not None
    document = json.loads(entry.metadata_path.read_text(encoding="utf-8"))
    document["from_the_future"] = {"colour": "green"}
    entry.metadata_path.write_text(json.dumps(document), encoding="utf-8")

    service.review("mega", key, verdict=ReviewVerdict.KEPT)

    written = json.loads(entry.metadata_path.read_text(encoding="utf-8"))
    assert written["from_the_future"] == {"colour": "green"}
    assert written["review"]["verdict"] == "kept"


def test_the_star_and_the_verdict_do_not_touch_each_other(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")

    service.review("mega", key, verdict=ReviewVerdict.KEPT)
    starred = service.review("mega", key, favourite=True)
    assert starred is not None
    assert starred.verdict is ReviewVerdict.KEPT
    assert starred.favourite is True

    judged = service.review("mega", key, verdict=ReviewVerdict.IGNORED)
    assert judged is not None
    assert judged.favourite is True


def test_a_verdict_can_be_taken_back(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")
    service.review("mega", key, verdict=ReviewVerdict.IGNORED)

    item = service.review("mega", key, verdict=ReviewVerdict.UNREVIEWED)

    assert item is not None
    assert item.verdict is ReviewVerdict.UNREVIEWED


def test_deciding_stamps_the_time_and_starring_does_not(tmp_path: Path) -> None:
    """`reviewed_at` answers "when was this decided", and a star is not a ruling."""
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")
    entry = library.entry_at("mega", key)
    assert entry is not None

    service.review("mega", key, favourite=True)
    record = entry.read()
    assert record is not None and record.review is not None
    assert record.review.reviewed_at is None

    service.review("mega", key, verdict=ReviewVerdict.KEPT)
    record = entry.read()
    assert record is not None and record.review is not None
    assert record.review.reviewed_at is not None


def test_judging_something_that_is_not_there_answers_nothing(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)

    assert service.review("mega", "nothing", verdict=ReviewVerdict.KEPT) is None
    assert service.review("..", "x", verdict=ReviewVerdict.KEPT) is None


def test_a_discarded_entry_is_out_of_the_way_until_it_is_asked_for(tmp_path: Path) -> None:
    """The whole meaning of the verdict: do not offer this to me again."""
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd", name="gone.pdf")
    write(library, "EeFfGgHh", name="kept.pdf")
    service.discard("mega", key)

    assert [item.name for item in service.browse().items] == ["kept.pdf"]
    assert [
        item.name for item in service.browse(LibraryQuery(verdict=ReviewVerdict.DISCARDED)).items
    ] == ["gone.pdf"]


def test_an_ignored_entry_stays_in_the_listing(tmp_path: Path) -> None:
    """Ignored means "not interesting", not "out of my way"."""
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")
    service.review("mega", key, verdict=ReviewVerdict.IGNORED)

    assert len(service.browse().items) == 1


def test_the_discarded_are_still_counted_so_they_can_be_found(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")
    service.discard("mega", key)

    page = service.browse()

    assert page.items == ()
    assert [(facet.value, facet.count) for facet in page.verdicts] == [("discarded", 1)]


def test_the_unreviewed_come_first_among_the_verdicts(tmp_path: Path) -> None:
    """Declaration order: the pile somebody works through leads the row."""
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")
    write(library, "EeFfGgHh")
    service.review("mega", key, verdict=ReviewVerdict.KEPT)

    assert [facet.value for facet in service.browse().verdicts] == ["unreviewed", "kept"]


def test_only_the_starred(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd", name="star.pdf")
    write(library, "EeFfGgHh", name="plain.pdf")
    service.review("mega", key, favourite=True)

    page = service.browse(LibraryQuery(favourite=True))

    assert [item.name for item in page.items] == ["star.pdf"]
    assert page.favourites == 1


def test_a_verdict_query_says_it_is_filtered() -> None:
    assert LibraryQuery(verdict=ReviewVerdict.KEPT).is_filtered is True
    assert LibraryQuery(favourite=True).is_filtered is True


def test_a_verdict_nobody_recognises_filters_nothing() -> None:
    assert parse_verdict("shrug") is None
    assert parse_verdict(None) is None
    assert parse_verdict("") is None
    assert parse_verdict("kept") is ReviewVerdict.KEPT


def test_the_service_refuses_to_write_a_discard(tmp_path: Path) -> None:
    """Removing the payload is what makes the word true; `discard` is its writer."""
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")

    with pytest.raises(ValueError, match="discarding"):
        service.review("mega", key, verdict=ReviewVerdict.DISCARDED)

    item = service.item("mega", key)
    assert item is not None
    assert item.verdict is ReviewVerdict.UNREVIEWED


# --- throwing something away --------------------------------------------------


def stored_review(library: Library, provider: str, key: str) -> dict[str, object]:
    """Return the review member of one entry's document, straight off disk.

    Read here rather than through the service because two of its fields are not
    on a listed item: when the payload went, and whether that is still recorded
    once the verdict is taken back.
    """
    entry = library.entry_at(provider, key)
    assert entry is not None
    document = json.loads(entry.metadata_path.read_text(encoding="utf-8"))
    review = document["review"]
    assert isinstance(review, dict)
    return review


def test_discarding_removes_the_file_and_says_so_in_one_call(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")
    item = service.item("mega", key)
    assert item is not None and item.path is not None

    discarded = service.discard("mega", key)

    assert discarded is not None
    assert discarded.verdict is ReviewVerdict.DISCARDED
    assert not item.path.exists()


def test_a_discarded_entry_still_says_what_it_used_to_hold(tmp_path: Path) -> None:
    """What goes is the bytes. A row nobody can read is not a row to sort by."""
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd", name="holiday.jpg", filename="holiday.jpg", size=4096)

    item = service.discard("mega", key)

    assert item is not None
    assert item.name == "holiday.jpg"
    assert item.filename == "holiday.jpg"
    assert item.size == 4096
    assert item.checksum == "abc123"


def test_discarding_records_when_the_payload_went(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")

    service.discard("mega", key)

    assert stored_review(library, "mega", key)["payload_removed_at"] is not None


def test_discarding_twice_keeps_the_moment_the_file_actually_went(tmp_path: Path) -> None:
    """It went when it went; pressing the button again does not move it."""
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")
    service.discard("mega", key)
    first = stored_review(library, "mega", key)["payload_removed_at"]

    service.discard("mega", key)

    assert stored_review(library, "mega", key)["payload_removed_at"] == first


def test_discarding_something_whose_file_is_already_gone_still_marks_it(tmp_path: Path) -> None:
    """A payload somebody moved away by hand is the state this produces."""
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd", payload=None)

    item = service.discard("mega", key)

    assert item is not None
    assert item.verdict is ReviewVerdict.DISCARDED


def test_discarding_leaves_the_star_alone(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")
    service.review("mega", key, favourite=True)

    item = service.discard("mega", key)

    assert item is not None
    assert item.favourite is True


def test_discarding_what_is_not_there_writes_nothing(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)

    assert service.discard("mega", "nothing") is None
    assert service.discard("..", "x") is None


def test_taking_a_discard_back_lifts_the_whole_headstone(tmp_path: Path) -> None:
    """Undo is one call, and what it undoes is the verdict, not the deletion.

    The removal time has to go with it: a record still carrying one would say the
    file had been deleted while the entry no longer claims anything of the sort —
    and a later download carries the review across untouched, so the lie would
    outlive the entry that told it.
    """
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")
    service.discard("mega", key)

    item = service.review("mega", key, verdict=ReviewVerdict.UNREVIEWED)

    assert item is not None
    assert item.verdict is ReviewVerdict.UNREVIEWED
    assert stored_review(library, "mega", key)["payload_removed_at"] is None


def test_starring_something_discarded_does_not_resurrect_it(tmp_path: Path) -> None:
    """Only the verdict owns the headstone, and the star is not a verdict."""
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")
    service.discard("mega", key)

    item = service.review("mega", key, favourite=True)

    assert item is not None
    assert item.verdict is ReviewVerdict.DISCARDED
    assert stored_review(library, "mega", key)["payload_removed_at"] is not None


def test_nothing_is_served_from_an_entry_whose_file_was_discarded(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")
    service.discard("mega", key)

    assert service.payload("mega", key) is None


# --- where one file stands in a listing ---------------------------------------


def test_a_place_says_which_of_how_many(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="a.pdf")
    middle = write(library, "EeFfGgHh", name="b.pdf")
    write(library, "IiJjKkLl", name="c.pdf")

    place = service.locate("mega", middle, LibraryQuery(sort=LibrarySort.NAME, descending=False))

    assert place is not None
    assert place.position == 2
    assert place.total == 3
    assert place.previous is not None and place.previous.name == "a.pdf"
    assert place.following is not None and place.following.name == "c.pdf"


def test_a_walk_has_two_ends(tmp_path: Path) -> None:
    """Not wrapped around: a walk that starts again cannot be finished."""
    service, library = make_service(tmp_path)
    first = write(library, "AaBbCcDd", name="a.pdf")
    last = write(library, "EeFfGgHh", name="b.pdf")
    ascending = LibraryQuery(sort=LibrarySort.NAME, descending=False)

    beginning = service.locate("mega", first, ascending)
    end = service.locate("mega", last, ascending)

    assert beginning is not None and beginning.previous is None
    assert end is not None and end.following is None


def test_the_neighbours_are_the_ones_of_that_filter(tmp_path: Path) -> None:
    """Walking a filter of forty is the job; walking nine thousand is not."""
    service, library = make_service(tmp_path)
    write(library, "AaBbCcDd", name="a.pdf", filename="a.pdf")
    middle = write(library, "EeFfGgHh", name="b.png", filename="b.png")
    write(library, "IiJjKkLl", name="c.png", filename="c.png")

    place = service.locate(
        "mega",
        middle,
        LibraryQuery(kind=MediaKind.IMAGE, sort=LibrarySort.NAME, descending=False),
    )

    assert place is not None
    assert place.total == 2
    assert place.previous is None
    assert place.following is not None and place.following.name == "c.png"


def test_a_walk_does_not_stop_at_a_page_boundary(tmp_path: Path) -> None:
    """Somebody moving from one file to the next is walking the whole result."""
    service, library = make_service(tmp_path)
    keys = [write(library, handle, name=f"{handle}.pdf") for handle in ("AaBb", "CcDd", "EeFf")]

    place = service.locate(
        "mega", keys[0], LibraryQuery(per_page=1, sort=LibrarySort.NAME, descending=False)
    )

    assert place is not None
    assert place.total == 3
    assert place.following is not None


def test_a_file_outside_the_listing_has_no_place_in_it(tmp_path: Path) -> None:
    """Opening something discarded from a listing that hides the discarded."""
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")
    service.discard("mega", key)

    assert service.locate("mega", key) is None
    assert service.locate("mega", key, LibraryQuery(verdict=ReviewVerdict.DISCARDED)) is not None


def test_locating_something_that_is_not_there_is_not_a_place(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)

    assert service.locate("mega", "nothing") is None


# --- what a report is told not to offer again ---------------------------------


def source_url(handle: str, provider: str = "mega") -> str:
    """Return the URL `write` records for *handle*."""
    return f"https://{provider}.nz/file/{handle}"


def test_an_ignored_entry_puts_its_link_out_of_reach(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")
    service.review("mega", key, verdict=ReviewVerdict.IGNORED)

    assert service.dismissed([source_url("AaBbCcDd")]) == frozenset({source_url("AaBbCcDd")})


def test_a_discarded_entry_does_the_same(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")
    service.discard("mega", key)

    assert service.dismissed([source_url("AaBbCcDd")]) == frozenset({source_url("AaBbCcDd")})


def test_what_was_kept_or_never_looked_at_is_not_dismissed(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    kept = write(library, "AaBbCcDd")
    write(library, "EeFfGgHh")
    service.review("mega", kept, verdict=ReviewVerdict.KEPT)

    assert service.dismissed([source_url("AaBbCcDd"), source_url("EeFfGgHh")]) == frozenset()


def test_a_link_nothing_was_recorded_under_is_not_dismissed(tmp_path: Path) -> None:
    """Nobody has said anything about it, which is where every URL starts."""
    service, _ = make_service(tmp_path)

    assert service.dismissed(["https://mega.nz/file/Unknown"]) == frozenset()


def test_one_dismissed_file_does_not_put_a_whole_folder_out_of_reach(tmp_path: Path) -> None:
    """A container is recorded by each file inside it, under its own URL.

    So *something here is unwanted* would take a folder of two hundred out of
    circulation because of one thumbnail — and the download of the container is
    still the right thing to queue while a single file in it is wanted.
    """
    service, library = make_service(tmp_path)
    container = "https://mega.nz/folder/FolderAA"
    first = write(library, "AaBbCcDd", source_url=container)
    write(library, "EeFfGgHh", source_url=container)
    service.review("mega", first, verdict=ReviewVerdict.IGNORED)

    assert service.dismissed([container]) == frozenset()


def test_a_folder_whose_every_file_was_waved_away_is_dismissed(tmp_path: Path) -> None:
    service, library = make_service(tmp_path)
    container = "https://mega.nz/folder/FolderAA"
    first = write(library, "AaBbCcDd", source_url=container)
    second = write(library, "EeFfGgHh", source_url=container)
    service.review("mega", first, verdict=ReviewVerdict.IGNORED)
    service.discard("mega", second)

    assert service.dismissed([container]) == frozenset({container})


def test_the_answer_keeps_the_fragment_it_was_asked_with(tmp_path: Path) -> None:
    """A share link carries its key there; a record never does (ADR-020)."""
    service, library = make_service(tmp_path)
    key = write(library, "AaBbCcDd")
    service.discard("mega", key)
    asked = f"{source_url('AaBbCcDd')}#0123456789abcdef"

    assert service.dismissed([asked]) == frozenset({asked})


def test_asking_about_nothing_reads_no_library(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)

    assert service.dismissed([]) == frozenset()


def test_a_discarded_entry_shows_a_symbol_rather_than_a_missing_picture(tmp_path: Path) -> None:
    service, library = make_service(tmp_path, preview_inline_bytes=1_000_000)
    key = write(library, "AaBbCcDd", name="shot.png", filename="shot.png", size=len(PAYLOAD))
    service.discard("mega", key)
    item = service.item("mega", key)
    assert item is not None

    assert service.preview(item).shape is PreviewShape.SYMBOL
