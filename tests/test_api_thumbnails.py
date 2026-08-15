"""Tests for serving the small copies, over HTTP.

End to end where it matters: a real image is stored, a real thumbnail is made
from it, and the bytes that come back over the route are decoded again. What is
under test is the arrangement — which route answers, what it says the answer is,
and what a tile links to — rather than the making, which is tested where the
cache is.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from maxicrawler.api import create_app
from maxicrawler.app import CrawlService, LibraryService
from maxicrawler.app.thumbnails import ThumbnailCache, cache_beside
from maxicrawler.config import Settings
from maxicrawler.domain import ResourceKind, ResourceRef
from maxicrawler.library import Library

pillow = pytest.importorskip("PIL", reason="the thumbnails extra is not installed")
Image = pillow.Image


def store(library: Library, handle: str, *, name: str, payload: bytes) -> str:
    """Write one library entry holding *payload* and return its key."""
    ref = ResourceRef(
        provider="mega",
        resource_id=handle,
        kind=ResourceKind.FILE,
        url=f"https://mega.nz/file/{handle}",
    )
    entry = library.entry(ref)
    entry.path.mkdir(parents=True, exist_ok=True)
    stored = entry.content_path(name)
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(payload)
    entry.metadata_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "provider": "mega",
                "key": entry.key,
                "resource_id": handle,
                "parent_id": None,
                "kind": "file",
                "name": name,
                "source_url": ref.url,
                "source_document": None,
                "status": "completed",
                "discovered_at": None,
                "downloaded_at": datetime(2026, 8, 9, 14, 30, tzinfo=UTC).isoformat(),
                "attempts": 1,
                "error": None,
                "content": {
                    "filename": name,
                    "path": f"content/{name}",
                    "size": len(payload),
                    "checksums": [{"algorithm": "sha256", "value": "a" * 64}],
                },
            }
        ),
        encoding="utf-8",
    )
    return entry.key


def a_photograph(width: int = 3000, height: int = 2000) -> bytes:
    """Return the bytes of a JPEG of that size."""
    buffer = BytesIO()
    Image.new("RGB", (width, height), "teal").save(buffer, "JPEG")
    return buffer.getvalue()


@contextmanager
def client(
    tmp_path: Path, **overrides: object
) -> Iterator[tuple[TestClient, LibraryService, Library]]:
    """Yield a client over an application whose library is below *tmp_path*."""
    settings = Settings(
        database_path=tmp_path / "urls.db",
        library_path=tmp_path / "library",
        min_download_size=0,
        **overrides,  # type: ignore[arg-type]
    )
    library = Library(settings.library_path)
    service = LibraryService(settings, library=library)
    application = create_app(service=CrawlService(settings), library=service)
    with TestClient(application) as test_client:
        yield test_client, service, library


def make_the_thumbnail(service: LibraryService, provider: str, key: str) -> None:
    """Run the maker over one entry, the way its own job will."""
    item = service.item(provider, key)
    assert item is not None
    cache = ThumbnailCache(cache_beside(service.settings.database_path))
    made = cache.make(item.path, service.thumbnail_key(item))  # type: ignore[arg-type]
    assert made is not None


def test_a_thumbnail_that_exists_comes_back_as_a_picture(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, service, library):
        key = store(library, "AaBbCcDd", name="holiday.jpg", payload=a_photograph())
        make_the_thumbnail(service, "mega", key)

        response = test_client.get(f"/library/mega/{key}/thumb")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/webp"
        # Decoded rather than counted: bytes with the right header and the wrong
        # content would pass a length check and show a broken image.
        with Image.open(BytesIO(response.content)) as returned:
            assert returned.format == "WEBP"
            assert max(returned.size) == 240


def test_a_thumbnail_says_it_must_not_be_sniffed(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, service, library):
        key = store(library, "AaBbCcDd", name="holiday.jpg", payload=a_photograph())
        make_the_thumbnail(service, "mega", key)

        response = test_client.get(f"/library/mega/{key}/thumb")

        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"


def test_an_entry_with_no_thumbnail_answers_404(tmp_path: Path) -> None:
    """An ordinary state: the maker has not been over this entry yet."""
    with client(tmp_path) as (test_client, _, library):
        key = store(library, "AaBbCcDd", name="holiday.jpg", payload=a_photograph())

        assert test_client.get(f"/library/mega/{key}/thumb").status_code == 404


def test_a_file_that_is_not_a_picture_answers_404(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, _, library):
        key = store(library, "AaBbCcDd", name="paper.pdf", payload=b"%PDF-1.4 ...")

        assert test_client.get(f"/library/mega/{key}/thumb").status_code == 404


def test_a_name_that_is_not_an_entry_answers_404(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, _, _library):
        assert test_client.get("/library/mega/nothing/thumb").status_code == 404
        assert test_client.get("/library/../../etc/thumb").status_code == 404


def test_the_route_never_makes_one(tmp_path: Path) -> None:
    """Sixty tiles must not be sixty image decodes inside sixty requests."""
    with client(tmp_path) as (test_client, service, library):
        key = store(library, "AaBbCcDd", name="holiday.jpg", payload=a_photograph())

        assert test_client.get(f"/library/mega/{key}/thumb").status_code == 404
        assert test_client.get(f"/library/mega/{key}/thumb").status_code == 404
        cache = cache_beside(service.settings.database_path)
        assert not cache.exists() or not list(cache.rglob("*.webp"))


def test_a_tile_links_to_the_thumbnail_once_there_is_one(tmp_path: Path) -> None:
    """And to the stored file until then, which is the old behaviour."""
    with client(tmp_path) as (test_client, service, library):
        key = store(library, "AaBbCcDd", name="holiday.jpg", payload=a_photograph())

        before = test_client.get("/library?view=grid").text
        assert f"/library/mega/{key}/view" in before
        assert f"/library/mega/{key}/thumb" not in before

        make_the_thumbnail(service, "mega", key)

        after = test_client.get("/library?view=grid").text
        assert f"/library/mega/{key}/thumb" in after
        assert f"/library/mega/{key}/view" not in after


def test_a_large_picture_gets_a_tile_it_never_had(tmp_path: Path) -> None:
    """Over the byte limit it used to be a symbol and nothing else.

    Half the gain on a library of photographs, and the half that is easy to
    miss: nearly half of one real library's images are above that limit.
    """
    with client(tmp_path, preview_inline_bytes=1000) as (test_client, service, library):
        key = store(library, "AaBbCcDd", name="raw.jpg", payload=a_photograph(4000, 3000))

        before = test_client.get("/library?view=grid").text
        assert "shot-symbol" in before

        make_the_thumbnail(service, "mega", key)
        after = test_client.get("/library?view=grid").text

        assert f"/library/mega/{key}/thumb" in after
        assert "shot-symbol" not in after
