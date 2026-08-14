"""End-to-end proof that an ordinary file now reaches the library.

Everything else about the direct provider is tested against the provider
itself. This file is about the wiring: real settings, the real registry, a real
socket, and a file on disk at the end of it. It is the assertion that would
have failed before this existed, and the one an operator actually cares about.
"""

from pathlib import Path

import pytest
from web_server import Site, serve

from maxicrawler.app import DownloadService, LibraryService
from maxicrawler.config import Settings
from maxicrawler.domain import DownloadStatus

IMAGE = b"\x89PNG\r\n\x1a\n" + b"p" * 4000
MEGA_LINK = "https://mega.nz/file/AaBbCcDd#0123456789abcdefghijklmnopqrstuvwxyzABC"


def make_service(tmp_path: Path, **overrides: object) -> DownloadService:
    """Return a service storing below *tmp_path*.

    Loopback is allowed, because the server every test here reaches is on
    127.0.0.1 and the shipped default refuses that. The test that wants the
    default's own behaviour builds a service without this.
    """
    overrides.setdefault("allow_private_networks", True)
    # And no floor, for the same shape of reason: the fixtures here serve a
    # handful of bytes, and this suite is about reaching an ordinary URL.
    overrides.setdefault("min_download_size", 0)
    settings = Settings(
        user_agent="MaxiCrawler/test",
        library_path=tmp_path / "library",
        database_path=tmp_path / "urls.db",
        **overrides,  # type: ignore[arg-type]
    )
    return DownloadService(settings)


def make_site() -> Site:
    """Return a site serving one image and one page."""
    site = Site()
    site.add("/hr/1234.png", body=IMAGE, content_type="image/png")
    site.add_html("/hr/", "<p>a board</p>")
    return site


def test_an_image_is_downloadable_at_all(tmp_path: Path) -> None:
    """The question that used to answer no for everything but Mega."""
    service = make_service(tmp_path)

    assert service.can_download("https://i.example.test/hr/1234.png") is True


def test_a_mega_link_is_still_answered_by_mega(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    assert service.can_download(MEGA_LINK) is True


def test_something_that_is_not_a_url_is_still_nobody_s(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    assert service.can_download("mailto:someone@example.test") is False


def test_a_batch_is_answered_without_a_single_request(tmp_path: Path) -> None:
    """What lets a report of two hundred links ask this while it renders."""
    service = make_service(tmp_path)
    site = make_site()

    with serve(site) as base:
        answers = service.downloadable([f"{base}/hr/1234.png", f"{base}/hr/", "mailto:a@b.test"])

    assert answers == {f"{base}/hr/1234.png", f"{base}/hr/"}
    assert site.requests == []


def test_an_image_arrives_in_the_library(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    with serve(make_site()) as base:
        summary = service.download(f"{base}/hr/1234.png")

    assert summary.succeeded is True
    (item,) = LibraryService(service.settings).browse().items
    assert item.status is DownloadStatus.COMPLETED
    assert item.filename == "1234.png"
    assert item.size == len(IMAGE)
    assert item.path is not None
    assert item.path.read_bytes() == IMAGE


def test_the_stored_record_remembers_where_it_came_from(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    with serve(make_site()) as base:
        service.download(f"{base}/hr/1234.png")
        expected = f"{base}/hr/1234.png"

    (item,) = LibraryService(service.settings).browse().items
    assert item.source_url == expected
    # The registry name, as it is for Mega: a record addresses an entry on
    # disk, and the display name is free to change without moving anything.
    assert item.provider == "direct"


def test_the_same_url_twice_is_one_entry(tmp_path: Path) -> None:
    """The identity is the URL, so a second run is the same place on disk."""
    service = make_service(tmp_path)

    with serve(make_site()) as base:
        service.download(f"{base}/hr/1234.png")
        service.download(f"{base}/hr/1234.png")

    assert len(LibraryService(service.settings).browse().items) == 1


def test_a_hostile_stated_name_cannot_escape_the_library(tmp_path: Path) -> None:
    """The pairing the transport deliberately leaves to the library.

    The provider reports what the header said; nothing between here and disk
    trusts it.
    """
    site = Site()
    site.add(
        "/get",
        body=IMAGE,
        headers=(("Content-Disposition", 'attachment; filename="../../../escaped.png"'),),
    )
    service = make_service(tmp_path)

    with serve(site) as base:
        summary = service.download(f"{base}/get")

    assert summary.succeeded is True
    (item,) = LibraryService(service.settings).browse().items
    assert item.path is not None
    assert item.path.is_relative_to(service.library_root)
    assert not (tmp_path / "escaped.png").exists()


def test_a_missing_file_is_a_finding_rather_than_a_crash(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    with serve(make_site()) as base:
        summary = service.download(f"{base}/gone.png")

    assert summary.succeeded is False


def test_the_shipped_configuration_will_not_fetch_from_this_machine(tmp_path: Path) -> None:
    """The guard reaches all the way through the service, not just the transport.

    A URL naming loopback arrives from a browser as readily as from an
    operator, and the download side had no guard at all before this.
    """
    service = make_service(tmp_path, allow_private_networks=False)
    site = make_site()

    with serve(site) as base:
        summary = service.download(f"{base}/hr/1234.png")

    assert summary.succeeded is False
    assert site.requests == []


@pytest.mark.parametrize("path", ["/hr/1234.png", "/hr/"])
def test_a_page_is_downloadable_too_and_that_is_the_honest_answer(
    tmp_path: Path, path: str
) -> None:
    """It really can fetch a page. Telling an image from one is the URL's job.

    `TargetKind` reads the suffix and is what a report filters by; "could this
    be downloaded" stopped being the discriminating question the moment
    anything claimed ordinary URLs.
    """
    service = make_service(tmp_path)

    with serve(make_site()) as base:
        assert service.can_download(f"{base}{path}") is True


# --- whether the question is a filter at all ---------------------------------


def test_an_installation_that_fetches_anything_says_so(tmp_path: Path) -> None:
    """What decides whether "can this be downloaded?" separates a report.

    With the direct provider composed for transfer it is a constant rather than
    a filter, and every recorded link answers yes.
    """
    assert make_service(tmp_path).downloads_ordinary_urls() is True


def test_an_inspection_only_installation_says_so_too(tmp_path: Path) -> None:
    service = make_service(tmp_path, direct_downloads=False)

    assert service.downloads_ordinary_urls() is False
    assert service.can_download("https://i.example.test/hr/1234.png") is False


def test_turning_it_off_leaves_the_other_providers_working(tmp_path: Path) -> None:
    """Off is not "no downloads"; it is "no downloads from anywhere at all"."""
    service = make_service(tmp_path, direct_downloads=False)

    assert service.can_download(MEGA_LINK) is True
