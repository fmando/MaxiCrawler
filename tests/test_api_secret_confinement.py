"""The decryption key must not leave the queue that holds it.

A Mega share carries its key in the URL fragment. Since Sprint 15 the queue
keeps the whole link until the transfer runs — and, for a retry, until the run
is evicted. That is a longer life than it had before, so the rule it lives under
is checked here rather than asserted in a docstring.

The rule is not "the key is never in memory": discovery already writes it to
SQLite and the report renders it into a table, because a share link *is* its
key and a link without one leads nowhere. The rule is that nothing the queue
produces — no snapshot, no rendered page, no event frame, no log line, no
redirect — carries it.

Modelled on `tests/test_mega_secret_confinement.py`, which does the same for
`ResourceSecret` a few layers down.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from doubles import StubProvider
from starlette.testclient import TestClient

from maxicrawler.api import create_app
from maxicrawler.api.downloads import TransferQueue
from maxicrawler.api.stream import download_payload
from maxicrawler.app import CrawlService, DownloadService, LibraryService
from maxicrawler.config import Settings
from maxicrawler.domain import ProviderCapability
from maxicrawler.library import Library
from maxicrawler.providers import ProviderRegistry

KEY = "abcdefghijklmnopqrstuvwxyz0123456789ABCD"
"""Long and distinctive, so finding it anywhere is unambiguous."""

FILE_URL = f"https://mega.nz/file/AaBbCcDd#{KEY}"
BARE_URL = "https://mega.nz/file/AaBbCcDd"
PAYLOAD = b"stub payload"
DOWNLOADS = frozenset({ProviderCapability.INSPECT, ProviderCapability.DOWNLOAD})


def _settings(tmp_path: Path) -> Settings:
    """Return throwaway settings storing below *tmp_path*."""
    return Settings(
        user_agent="MaxiCrawler/test",
        database_path=tmp_path / "urls.db",
        library_path=tmp_path / "library",
    )


def _service(tmp_path: Path) -> DownloadService:
    """Return a download service over a stub provider and a throwaway library."""
    settings = _settings(tmp_path)
    library = Library(settings.library_path)
    return DownloadService(
        settings,
        providers=ProviderRegistry(
            [
                StubProvider(
                    "mega",
                    url_prefix="https://mega.nz/",
                    capabilities=DOWNLOADS,
                    payload=PAYLOAD,
                )
            ]
        ),
        library=library,
    )


@contextmanager
def queued(tmp_path: Path) -> Iterator[tuple[TestClient, TransferQueue]]:
    """Yield a client whose queue holds paused requests carrying a key."""
    settings = _settings(tmp_path)
    library = Library(settings.library_path)
    downloads = TransferQueue(_service(tmp_path))
    downloads.pause()
    application = create_app(
        service=CrawlService(settings),
        downloads=downloads,
        library=LibraryService(settings, library=library),
    )
    try:
        with TestClient(application) as client:
            yield client, downloads
    finally:
        downloads.shutdown()


def test_the_run_never_learns_the_key(tmp_path: Path) -> None:
    with queued(tmp_path) as (_, downloads):
        run = downloads.submit(FILE_URL)

        assert run.url == BARE_URL
        assert KEY not in repr(run.__dict__)


def test_no_snapshot_carries_the_key(tmp_path: Path) -> None:
    with queued(tmp_path) as (_, downloads):
        run = downloads.submit(FILE_URL)

        assert KEY not in repr(run.snapshot())
        assert KEY not in repr(downloads.snapshot())


def test_no_event_frame_carries_the_key(tmp_path: Path) -> None:
    """What a browser watching a transfer receives, which is JSON over the wire."""
    with queued(tmp_path) as (_, downloads):
        run = downloads.submit(FILE_URL)

        assert KEY not in json.dumps(download_payload(run.snapshot()))


def test_the_page_of_a_queued_download_does_not_show_the_key(tmp_path: Path) -> None:
    with queued(tmp_path) as (client, downloads):
        run = downloads.submit(FILE_URL)

        body = client.get(f"/downloads/{run.id}").text

    assert BARE_URL in body
    assert KEY not in body


def test_the_redirect_after_queueing_does_not_carry_the_key(tmp_path: Path) -> None:
    """It would end up in a browser's history and in every proxy log on the way."""
    with queued(tmp_path) as (client, _):
        response = client.post("/downloads", data={"url": FILE_URL}, follow_redirects=False)

    assert response.status_code == 303
    assert KEY not in response.headers["location"]


def test_a_link_that_is_refused_is_echoed_without_its_key(tmp_path: Path) -> None:
    """A rejected link goes back to whoever sent it, message and all."""
    with queued(tmp_path) as (client, _):
        response = client.post(
            "/downloads", data={"url": f"not-a-url#{KEY}"}, follow_redirects=False
        )

    assert response.status_code == 400
    assert KEY not in response.text


def test_the_key_goes_when_the_run_does(tmp_path: Path) -> None:
    """What bounds how long it is held: eviction, the same as everything else."""
    with queued(tmp_path) as (_, downloads):
        run = downloads.submit(FILE_URL)
        downloads.cancel(run.id)

        # Still held while the run is, which is what a retry needs.
        assert downloads.retry(run.id) is not None

    small = TransferQueue(_service(tmp_path), retain=1)
    small.pause()
    try:
        first = small.submit(FILE_URL)
        small.cancel(first.id)
        second = small.submit(f"{BARE_URL}?b#{KEY}")
        small.cancel(second.id)
        small.submit(f"{BARE_URL}?c#{KEY}")

        assert small.get(first.id) is None
        assert small.retry(first.id) is None
        assert KEY not in repr(small.snapshot())
    finally:
        small.shutdown()
