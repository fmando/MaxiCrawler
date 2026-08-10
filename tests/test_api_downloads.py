"""Tests for downloads running on a worker thread.

The provider is a stub, so nothing here opens a socket; what is under test is
the registry — one at a time, what a snapshot says while a transfer runs, what
survives it, and what is refused.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import pytest
from doubles import StubProvider

from maxicrawler.api.downloads import DownloadRun, DownloadRuns, DownloadSnapshot
from maxicrawler.api.errors import DownloadBusyError
from maxicrawler.api.stream import download_events, download_payload
from maxicrawler.app import DownloadService
from maxicrawler.config import Settings
from maxicrawler.domain import ContentDescriptor, DownloadStatus, ProviderCapability, ResourceRef
from maxicrawler.library import Library
from maxicrawler.providers import DownloadSink, ProviderRegistry

KEY = "0123456789abcdefghijkl"
FILE_URL = f"https://mega.nz/file/AaBbCcDd#{KEY}"
OTHER_URL = f"https://mega.nz/file/EeFfGgHh#{KEY}"
PAYLOAD = b"stub payload"
DOWNLOADS = frozenset({ProviderCapability.INSPECT, ProviderCapability.DOWNLOAD})


class BlockingProvider(StubProvider):
    """A stub whose transfer waits until a test lets it finish."""

    def __init__(self) -> None:
        super().__init__(
            "mega", url_prefix="https://mega.nz/", capabilities=DOWNLOADS, payload=PAYLOAD
        )
        self.transferring = Event()
        self.release = Event()

    def download(self, ref: ResourceRef, sink: DownloadSink) -> ContentDescriptor:
        descriptor = ContentDescriptor(name="stub.bin", size=len(PAYLOAD))
        sink.begin(descriptor)
        sink.write(PAYLOAD[:4])
        self.transferring.set()
        self.release.wait(timeout=10)
        sink.write(PAYLOAD[4:])
        return descriptor


def make_service(tmp_path: Path, provider: StubProvider | None = None) -> DownloadService:
    """Return a service storing into a library below *tmp_path*."""
    library = Library(tmp_path / "library")
    registry = ProviderRegistry(
        [
            provider
            if provider is not None
            else StubProvider(
                "mega", url_prefix="https://mega.nz/", capabilities=DOWNLOADS, payload=PAYLOAD
            )
        ]
    )
    return DownloadService(Settings(library_path=library.root), providers=registry, library=library)


@contextmanager
def registry(tmp_path: Path, provider: StubProvider | None = None) -> Iterator[DownloadRuns]:
    """Yield a registry and shut its worker down afterwards."""
    runs = DownloadRuns(make_service(tmp_path, provider))
    try:
        yield runs
    finally:
        runs.shutdown()


def wait_for(run: DownloadRun, *, timeout: float = 10.0) -> DownloadSnapshot:
    """Return the run's snapshot once it has finished."""
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        snapshot = run.snapshot()
        if snapshot.is_finished:
            return snapshot
        sleep(0.01)
    raise AssertionError(f"the download did not finish within {timeout}s")


# --- running ------------------------------------------------------------------


def test_a_submitted_download_runs_and_reports(tmp_path: Path) -> None:
    with registry(tmp_path) as runs:
        run = runs.submit(FILE_URL)
        snapshot = wait_for(run)

    assert snapshot.status is DownloadStatus.COMPLETED
    assert snapshot.label == "stub.bin"
    assert snapshot.path is not None
    assert snapshot.path.read_bytes() == PAYLOAD
    assert snapshot.summary is not None
    assert snapshot.elapsed_seconds >= 0


def test_a_download_starts_out_pending(tmp_path: Path) -> None:
    """Planning inspects the link, so there is a moment before any byte moves."""
    provider = BlockingProvider()
    with registry(tmp_path, provider) as runs:
        run = DownloadRun("id", "https://mega.nz/file/AaBbCcDd")

        snapshot = run.snapshot()

        assert snapshot.status is DownloadStatus.PENDING
        assert snapshot.is_finished is False
        assert runs.active() is None


def test_a_running_download_reports_what_has_arrived(tmp_path: Path) -> None:
    provider = BlockingProvider()
    with registry(tmp_path, provider) as runs:
        run = runs.submit(FILE_URL)
        assert provider.transferring.wait(timeout=10)

        running = run.snapshot()
        provider.release.set()
        finished = wait_for(run)

    assert running.status is DownloadStatus.RUNNING
    assert running.progress.bytes_written == 4
    assert running.progress.total_bytes == 1024
    assert running.is_finished is False
    assert finished.status is DownloadStatus.COMPLETED


def test_the_registry_knows_which_download_is_running(tmp_path: Path) -> None:
    provider = BlockingProvider()
    with registry(tmp_path, provider) as runs:
        run = runs.submit(FILE_URL)
        assert provider.transferring.wait(timeout=10)

        assert runs.active() is run

        provider.release.set()
        wait_for(run)
        # The slot is released by the worker, a moment after the run is done.
        deadline = monotonic() + 5
        while runs.active() is not None and monotonic() < deadline:
            sleep(0.01)
        assert runs.active() is None


def test_a_second_download_is_refused_rather_than_queued(tmp_path: Path) -> None:
    provider = BlockingProvider()
    with registry(tmp_path, provider) as runs:
        first = runs.submit(FILE_URL)
        assert provider.transferring.wait(timeout=10)

        with pytest.raises(DownloadBusyError, match="one at a time"):
            runs.submit(OTHER_URL)

        provider.release.set()
        wait_for(first)


def test_a_bad_url_is_refused_before_a_thread_starts(tmp_path: Path) -> None:
    with registry(tmp_path) as runs:
        with pytest.raises(ValueError, match="not an absolute HTTP"):
            runs.submit(str(tmp_path))

        assert runs.recent() == ()
        assert runs.active() is None


def test_a_download_that_raises_below_us_is_recorded_rather_than_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed transfer is a summary; only a fault on our side arrives here."""
    with registry(tmp_path) as runs:
        monkeypatch.setattr(
            runs.service,
            "download",
            lambda *_, **__: (_ for _ in ()).throw(RuntimeError("disk on fire")),
        )

        snapshot = wait_for(runs.submit(FILE_URL))

    assert snapshot.status is DownloadStatus.FAILED
    assert snapshot.error == "RuntimeError: disk on fire"
    assert snapshot.reason == "RuntimeError: disk on fire"


# --- what the registry keeps --------------------------------------------------


def test_a_finished_download_can_be_found_again(tmp_path: Path) -> None:
    with registry(tmp_path) as runs:
        run = runs.submit(FILE_URL)
        wait_for(run)

        assert runs.get(run.id) is run
        assert runs.get("nothing") is None


def test_finished_downloads_are_evicted_beyond_the_limit(tmp_path: Path) -> None:
    """Checked when the next one is submitted, so a run is never evicted mid-flight."""
    runs = DownloadRuns(make_service(tmp_path), retain=1)
    try:
        first = runs.submit(FILE_URL)
        wait_for(first)
        second = runs.submit(OTHER_URL)
        wait_for(second)
        third = runs.submit(FILE_URL)
        wait_for(third)
    finally:
        runs.shutdown()

    assert runs.get(first.id) is None
    assert runs.get(second.id) is second
    assert runs.get(third.id) is third


def test_a_registry_keeps_at_least_one(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        DownloadRuns(make_service(tmp_path), retain=0)


# --- the key ------------------------------------------------------------------


def test_the_run_never_learns_the_key(tmp_path: Path) -> None:
    with registry(tmp_path) as runs:
        run = runs.submit(FILE_URL)
        snapshot = wait_for(run)

    assert run.url == "https://mega.nz/file/AaBbCcDd"
    assert KEY not in json.dumps(download_payload(snapshot))
    assert KEY not in repr(snapshot)


# --- watching it --------------------------------------------------------------


def test_a_listener_hears_every_change(tmp_path: Path) -> None:
    seen: list[DownloadSnapshot] = []
    with registry(tmp_path) as runs:
        run = runs.submit(FILE_URL)
        run.add_listener(seen.append)
        wait_for(run)

    assert seen
    assert seen[-1].is_finished is True


def test_a_removed_listener_hears_nothing_more() -> None:
    seen: list[DownloadSnapshot] = []
    run = DownloadRun("id", "https://mega.nz/file/AaBbCcDd")
    run.add_listener(seen.append)
    run.fail("first")
    run.remove_listener(seen.append)
    run.remove_listener(seen.append)

    run.fail("second")

    assert len(seen) == 1


def test_the_stream_describes_a_finished_download_and_stops() -> None:
    import asyncio

    run = DownloadRun("id", "https://mega.nz/file/AaBbCcDd")
    run.fail("gone")

    async def collect() -> list[str]:
        return [event.name async for event in download_events(run, heartbeat=0.05)]

    names = asyncio.run(asyncio.wait_for(collect(), timeout=5))

    assert names == ["progress", "finished"]
