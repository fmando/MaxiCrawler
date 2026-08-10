"""Tests for downloads waiting and running on a worker thread.

The provider is a stub, so nothing here opens a socket; what is under test is
the queue — the order it drains in, what a snapshot says while a request waits
and while it runs, what survives it, and what is refused.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from threading import enumerate as enumerate_threads
from time import monotonic, sleep

import pytest
from doubles import StubProvider

from maxicrawler.api.downloads import (
    DownloadRun,
    DownloadSnapshot,
    Move,
    TransferQueue,
)
from maxicrawler.api.errors import QueueFullError
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
def registry(
    tmp_path: Path, provider: StubProvider | None = None, **options: int
) -> Iterator[TransferQueue]:
    """Yield a queue and shut its worker down afterwards."""
    runs = TransferQueue(make_service(tmp_path, provider), **options)
    try:
        yield runs
    finally:
        runs.shutdown()


THIRD_URL = f"https://mega.nz/file/IiJjKkLl#{KEY}"


def bare(url: str) -> str:
    """Return *url* as a run holds it: without the fragment, without the key."""
    return url.split("#", 1)[0]


@contextmanager
def paused(tmp_path: Path, **options: int) -> Iterator[TransferQueue]:
    """Yield a queue that takes nothing off itself until a test says so.

    What makes the waiting side testable without a blocking provider: a request
    that is never picked up stays exactly where it was put.
    """
    with registry(tmp_path, **options) as runs:
        runs.pause()
        yield runs


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


def test_a_download_starts_out_waiting(tmp_path: Path) -> None:
    """Queued and running are both "nothing transferred"; only one is your turn."""
    provider = BlockingProvider()
    with registry(tmp_path, provider) as runs:
        run = DownloadRun("id", "https://mega.nz/file/AaBbCcDd")

        snapshot = run.snapshot()

        assert snapshot.status is DownloadStatus.PENDING
        assert snapshot.is_queued is True
        assert snapshot.is_running is False
        assert snapshot.is_finished is False
        assert snapshot.elapsed_seconds == 0.0
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


def test_the_queue_knows_which_download_is_running(tmp_path: Path) -> None:
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


def test_a_second_download_waits_its_turn(tmp_path: Path) -> None:
    """What ADR-026 refused and ADR-033 queues."""
    provider = BlockingProvider()
    with registry(tmp_path, provider) as runs:
        first = runs.submit(FILE_URL)
        assert provider.transferring.wait(timeout=10)

        second = runs.submit(OTHER_URL)

        assert second.snapshot().is_queued is True
        assert runs.position_of(second.id) == 1
        assert runs.active() is first

        provider.release.set()
        wait_for(first)
        wait_for(second)

        assert runs.position_of(second.id) is None


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
    runs = TransferQueue(make_service(tmp_path), retain=1)
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


def test_a_queue_keeps_at_least_one(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        TransferQueue(make_service(tmp_path), retain=0)


def test_a_queue_holds_at_least_one(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        TransferQueue(make_service(tmp_path), limit=0)


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


# --- stopping ------------------------------------------------------------------


def test_a_running_download_can_be_stopped(tmp_path: Path) -> None:
    """What Sprint 12 had no seam for: a transfer that is already moving."""
    provider = BlockingProvider()
    with registry(tmp_path, provider) as runs:
        run = runs.submit(FILE_URL)
        assert provider.transferring.wait(timeout=10)

        run.stop()
        provider.release.set()
        snapshot = wait_for(run)

    assert snapshot.status is DownloadStatus.CANCELLED
    assert snapshot.error is None


def test_a_stopped_download_stores_nothing(tmp_path: Path) -> None:
    provider = BlockingProvider()
    with registry(tmp_path, provider) as runs:
        run = runs.submit(FILE_URL)
        assert provider.transferring.wait(timeout=10)

        run.stop()
        provider.release.set()
        snapshot = wait_for(run)

    assert snapshot.path is None
    assert list((tmp_path / "library").rglob("*.bin")) == []


def test_a_stopped_download_is_not_reported_as_a_failure(tmp_path: Path) -> None:
    """The person reading the word is the person who pressed the button."""
    provider = BlockingProvider()
    with registry(tmp_path, provider) as runs:
        run = runs.submit(FILE_URL)
        assert provider.transferring.wait(timeout=10)

        run.stop()
        provider.release.set()
        snapshot = wait_for(run)

    assert snapshot.status is not DownloadStatus.FAILED
    assert snapshot.summary is not None
    assert snapshot.summary.succeeded is False


def test_the_slot_is_free_again_after_a_stop(tmp_path: Path) -> None:
    provider = BlockingProvider()
    with registry(tmp_path, provider) as runs:
        first = runs.submit(FILE_URL)
        assert provider.transferring.wait(timeout=10)
        first.stop()
        provider.release.set()
        wait_for(first)

        assert runs.active() is None


def test_shutting_down_stops_a_transfer_rather_than_waiting_for_it(tmp_path: Path) -> None:
    """The behaviour `serve` inherits.

    Before this there was no cooperative stop, so a server going down waited
    for the file -- on a large one, a shutdown that looked like a hang.
    """
    provider = BlockingProvider()
    runs = TransferQueue(make_service(tmp_path, provider))
    run = runs.submit(FILE_URL)
    assert provider.transferring.wait(timeout=10)

    runs.shutdown(wait=False)
    provider.release.set()
    snapshot = wait_for(run)

    assert snapshot.status is DownloadStatus.CANCELLED


def test_stopping_a_download_nobody_started_is_harmless(tmp_path: Path) -> None:
    with registry(tmp_path) as runs:
        runs.shutdown(wait=False)

        assert runs.active() is None


# --- the order it drains in ----------------------------------------------------


def waiting_urls(runs: TransferQueue) -> list[str]:
    """Return the URLs still waiting, in the order they would be taken."""
    return [snapshot.url for snapshot in runs.snapshot().waiting]


def test_requests_are_drained_in_the_order_they_arrived(tmp_path: Path) -> None:
    provider = BlockingProvider()
    with registry(tmp_path, provider) as runs:
        blocking = runs.submit(FILE_URL)
        assert provider.transferring.wait(timeout=10)
        runs.pause()

        first = runs.submit(OTHER_URL)
        second = runs.submit(THIRD_URL)

        assert waiting_urls(runs) == [bare(OTHER_URL), bare(THIRD_URL)]
        assert (runs.position_of(first.id), runs.position_of(second.id)) == (1, 2)

        provider.release.set()
        wait_for(blocking)


def test_a_waiting_request_can_be_moved_to_the_front(tmp_path: Path) -> None:
    with paused(tmp_path) as runs:
        runs.submit(FILE_URL)
        last = runs.submit(THIRD_URL)

        assert runs.move(last.id, Move.TOP) is True
        assert waiting_urls(runs) == [bare(THIRD_URL), bare(FILE_URL)]


def test_a_waiting_request_can_be_nudged_either_way(tmp_path: Path) -> None:
    with paused(tmp_path) as runs:
        first = runs.submit(FILE_URL)
        second = runs.submit(OTHER_URL)
        runs.submit(THIRD_URL)

        assert runs.move(second.id, Move.UP) is True
        assert waiting_urls(runs)[0] == bare(OTHER_URL)

        assert runs.move(first.id, Move.DOWN) is True
        assert waiting_urls(runs) == [bare(OTHER_URL), bare(THIRD_URL), bare(FILE_URL)]


def test_moving_the_one_at_the_front_up_changes_nothing(tmp_path: Path) -> None:
    with paused(tmp_path) as runs:
        first = runs.submit(FILE_URL)
        runs.submit(OTHER_URL)

        assert runs.move(first.id, Move.UP) is False
        assert waiting_urls(runs)[0] == bare(FILE_URL)


def test_only_a_waiting_request_can_be_moved(tmp_path: Path) -> None:
    """What is being transferred right now is not a position in a line."""
    provider = BlockingProvider()
    with registry(tmp_path, provider) as runs:
        run = runs.submit(FILE_URL)
        assert provider.transferring.wait(timeout=10)

        assert runs.move(run.id, Move.TOP) is False
        assert runs.move("nothing", Move.TOP) is False

        provider.release.set()
        wait_for(run)


# --- pausing -------------------------------------------------------------------


def test_a_paused_queue_takes_nothing_off(tmp_path: Path) -> None:
    with paused(tmp_path) as runs:
        run = runs.submit(FILE_URL)
        sleep(0.2)

        assert run.snapshot().is_queued is True
        assert runs.active() is None
        assert runs.snapshot().is_paused is True


def test_resuming_lets_the_queue_go_again(tmp_path: Path) -> None:
    with paused(tmp_path) as runs:
        run = runs.submit(FILE_URL)

        runs.resume()

        assert wait_for(run).status is DownloadStatus.COMPLETED
        assert runs.is_paused is False


def test_pausing_leaves_the_running_transfer_alone(tmp_path: Path) -> None:
    """Let-me-think and undo-what-is-happening are different intentions."""
    provider = BlockingProvider()
    with registry(tmp_path, provider) as runs:
        run = runs.submit(FILE_URL)
        assert provider.transferring.wait(timeout=10)

        runs.pause()
        provider.release.set()

        assert wait_for(run).status is DownloadStatus.COMPLETED


# --- taking one out ------------------------------------------------------------


def test_a_waiting_request_can_be_removed_before_it_starts(tmp_path: Path) -> None:
    with paused(tmp_path) as runs:
        run = runs.submit(FILE_URL)

        assert runs.cancel(run.id) is True

        snapshot = run.snapshot()
        assert snapshot.status is DownloadStatus.CANCELLED
        assert snapshot.is_finished is True
        assert snapshot.reason == "removed from the queue"
        assert waiting_urls(runs) == []


def test_removing_a_request_is_not_reported_as_a_failure(tmp_path: Path) -> None:
    with paused(tmp_path) as runs:
        run = runs.submit(FILE_URL)
        runs.cancel(run.id)

    assert run.snapshot().status is not DownloadStatus.FAILED


def test_cancelling_the_running_one_stops_it(tmp_path: Path) -> None:
    provider = BlockingProvider()
    with registry(tmp_path, provider) as runs:
        run = runs.submit(FILE_URL)
        assert provider.transferring.wait(timeout=10)

        assert runs.cancel(run.id) is True
        provider.release.set()

        assert wait_for(run).status is DownloadStatus.CANCELLED


def test_cancelling_something_already_over_says_so_rather_than_raising(tmp_path: Path) -> None:
    """A second click on a button that was true a moment ago is not an error."""
    with registry(tmp_path) as runs:
        run = runs.submit(FILE_URL)
        wait_for(run)

        assert runs.cancel(run.id) is False
        assert runs.cancel("nothing") is False


def test_a_removed_request_is_never_taken_off_the_queue(tmp_path: Path) -> None:
    with paused(tmp_path) as runs:
        removed = runs.submit(FILE_URL)
        kept = runs.submit(OTHER_URL)
        runs.cancel(removed.id)

        runs.resume()
        wait_for(kept)

    assert removed.snapshot().reason == "removed from the queue"
    assert kept.snapshot().status is DownloadStatus.COMPLETED


# --- trying again --------------------------------------------------------------


def test_a_finished_download_can_be_queued_again(tmp_path: Path) -> None:
    with registry(tmp_path) as runs:
        first = runs.submit(FILE_URL)
        wait_for(first)

        again = runs.retry(first.id)

        assert again is not None
        assert again.id != first.id
        assert again.url == first.url
        assert wait_for(again).status is DownloadStatus.SKIPPED


def test_retrying_keeps_the_key_the_transfer_will_need(tmp_path: Path) -> None:
    """A share carries its credential in the fragment, and a retry needs it again."""
    with paused(tmp_path) as runs:
        first = runs.submit(FILE_URL)
        runs.cancel(first.id)

        again = runs.retry(first.id)

        assert again is not None
        assert runs.snapshot().waiting[0].url == bare(FILE_URL)


def test_what_happened_the_first_time_is_not_overwritten(tmp_path: Path) -> None:
    with paused(tmp_path) as runs:
        first = runs.submit(FILE_URL)
        runs.cancel(first.id)

        assert runs.retry(first.id) is not None

        assert first.snapshot().status is DownloadStatus.CANCELLED
        assert first.snapshot().reason == "removed from the queue"


def test_there_is_nothing_to_retry_about_a_download_still_going(tmp_path: Path) -> None:
    with paused(tmp_path) as runs:
        run = runs.submit(FILE_URL)

        assert runs.retry(run.id) is None
        assert runs.retry("nothing") is None


# --- the ceiling ---------------------------------------------------------------


def test_a_full_queue_refuses_rather_than_growing(tmp_path: Path) -> None:
    with paused(tmp_path, limit=2) as runs:
        runs.submit(FILE_URL)
        runs.submit(OTHER_URL)

        with pytest.raises(QueueFullError, match="the queue is full"):
            runs.submit(THIRD_URL)

        assert len(runs.snapshot().waiting) == 2


def test_room_freed_by_a_removal_can_be_used_again(tmp_path: Path) -> None:
    with paused(tmp_path, limit=1) as runs:
        first = runs.submit(FILE_URL)
        runs.cancel(first.id)

        assert runs.submit(OTHER_URL) is not None


# --- what the queue says about itself ------------------------------------------


def test_a_snapshot_never_shows_one_request_in_two_places(tmp_path: Path) -> None:
    provider = BlockingProvider()
    with registry(tmp_path, provider) as runs:
        running = runs.submit(FILE_URL)
        assert provider.transferring.wait(timeout=10)
        waiting = runs.submit(OTHER_URL)

        snapshot = runs.snapshot()

        assert snapshot.active is not None
        assert snapshot.active.download_id == running.id
        assert [item.download_id for item in snapshot.waiting] == [waiting.id]
        assert snapshot.finished == ()
        assert snapshot.remaining == 2
        assert snapshot.is_busy is True

        provider.release.set()
        wait_for(running)
        wait_for(waiting)


def test_a_snapshot_counts_how_things_ended(tmp_path: Path) -> None:
    with paused(tmp_path) as runs:
        done = runs.submit(FILE_URL)
        removed = runs.submit(OTHER_URL)
        runs.cancel(removed.id)
        runs.resume()
        wait_for(done)

        snapshot = runs.snapshot()

    assert snapshot.succeeded == 1
    assert snapshot.stopped == 1
    assert snapshot.failed == 0
    assert snapshot.bytes_written == len(PAYLOAD)
    assert snapshot.is_busy is False


def test_the_newest_finished_download_is_listed_first(tmp_path: Path) -> None:
    with registry(tmp_path) as runs:
        first = runs.submit(FILE_URL)
        wait_for(first)
        second = runs.submit(OTHER_URL)
        wait_for(second)

        listed = [item.download_id for item in runs.snapshot().finished]

    assert listed == [second.id, first.id]


def test_an_empty_queue_says_it_has_nothing_to_do(tmp_path: Path) -> None:
    with registry(tmp_path) as runs:
        snapshot = runs.snapshot()

    assert (snapshot.active, snapshot.waiting, snapshot.finished) == (None, (), ())
    assert snapshot.is_busy is False
    assert snapshot.remaining == 0


def test_shutting_down_drops_what_was_still_waiting(tmp_path: Path) -> None:
    """A queue that lives in memory ends with the process."""
    provider = BlockingProvider()
    runs = TransferQueue(make_service(tmp_path, provider))
    running = runs.submit(FILE_URL)
    assert provider.transferring.wait(timeout=10)
    waiting = runs.submit(OTHER_URL)

    runs.shutdown(wait=False)
    provider.release.set()
    wait_for(running)

    assert waiting.snapshot().is_finished is False
    assert runs.snapshot().waiting == ()


def test_a_queue_that_never_downloads_starts_no_thread(tmp_path: Path) -> None:
    """Most applications only crawl, and a thread waiting forever is not free."""
    before = {thread.name for thread in enumerate_threads()}
    runs = TransferQueue(make_service(tmp_path))
    try:
        assert {thread.name for thread in enumerate_threads()} == before
    finally:
        runs.shutdown()
