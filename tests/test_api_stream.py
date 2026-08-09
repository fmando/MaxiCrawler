"""Tests for carrying crawl progress from a worker thread to the event loop.

No sockets and no crawls here: a `CrawlJob` can be built without starting one,
and events can be published onto its bus from a thread. That gives exact
control over the timing the bridge exists to handle.
"""

import asyncio
import json
import threading
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from maxicrawler.api.jobs import CrawlJob, JobSnapshot
from maxicrawler.api.stream import (
    DEFAULT_HEARTBEAT_SECONDS,
    ServerEvent,
    SnapshotListener,
    crawl_events,
    event_stream,
    snapshot_payload,
)
from maxicrawler.events import CrawlFinished, CrawlStarted, PageCrawled, PageFailed
from maxicrawler.web.session import CrawlOptions, CrawlSession, CrawlState


def make_job(**options: object) -> CrawlJob:
    """Return a job that is not running anything."""
    session = CrawlSession(
        session_id="job-1",
        seed_url="https://example.test/",
        started_at=datetime.now(UTC),
        options=CrawlOptions(**options),  # type: ignore[arg-type]
    )
    return CrawlJob(session)


def page_crawled(url: str = "https://example.test/a", links: int = 7) -> PageCrawled:
    """Return a page event as the engine would publish it."""
    return PageCrawled(
        session_id="job-1", url=url, final_url=url, depth=1, status=200, link_count=links
    )


def run[T](coroutine: Callable[[], Awaitable[T]], *, timeout: float = 5.0) -> T:
    """Run *coroutine* on a private loop, refusing to hang a test."""

    async def guarded() -> T:
        return await asyncio.wait_for(coroutine(), timeout=timeout)

    return asyncio.run(guarded())


# --- the wire format ---------------------------------------------------------


def test_an_event_renders_as_a_named_frame() -> None:
    frame = ServerEvent("progress", {"pages_visited": 3}).render()

    assert frame == 'event: progress\ndata: {"pages_visited":3}\n\n'


def test_a_frame_ends_with_a_blank_line() -> None:
    """Without it a browser never dispatches the event."""
    assert ServerEvent("progress", {}).render().endswith("\n\n")


def test_a_heartbeat_is_a_comment_carrying_nothing() -> None:
    assert ServerEvent.ping().render() == ": ping\n\n"


def test_payload_keys_are_stable() -> None:
    """The browser patches by key, so the order must not wander."""
    first = ServerEvent("progress", {"b": 1, "a": 2}).render()
    second = ServerEvent("progress", {"a": 2, "b": 1}).render()

    assert first == second


def test_a_payload_describes_the_snapshot() -> None:
    snapshot = JobSnapshot(
        job_id="job-1",
        seed_url="https://example.test/",
        state=CrawlState.RUNNING,
        options=CrawlOptions(max_pages=10),
        started_at=datetime.now(UTC),
        pages_visited=3,
        pages_failed=1,
        links_found=42,
        elapsed_seconds=1.2345,
        latest_url="https://example.test/a",
    )

    payload = snapshot_payload(snapshot)

    assert payload["pages_visited"] == 3
    assert payload["pages_failed"] == 1
    assert payload["pages_attempted"] == 4
    assert payload["links_found"] == 42
    assert payload["latest_url"] == "https://example.test/a"
    assert payload["is_finished"] is False


def test_the_payload_is_what_the_page_itself_shows() -> None:
    """One rendering, two channels. A second formatter in JavaScript would drift."""
    from maxicrawler.api.views import progress_view

    snapshot = make_job(max_pages=10).snapshot()

    assert snapshot_payload(snapshot) == progress_view(snapshot)


def test_the_payload_carries_values_already_formatted() -> None:
    snapshot = JobSnapshot(
        job_id="job-1",
        seed_url="https://example.test/",
        state=CrawlState.RUNNING,
        options=CrawlOptions(max_pages=10),
        started_at=datetime.now(UTC),
        pages_visited=4,
        elapsed_seconds=83.0,
    )

    payload = snapshot_payload(snapshot)

    assert payload["elapsed"] == "1 min 23 s"
    assert payload["progress_percent"] == 40
    assert payload["state_label"] == "running"


def test_a_payload_survives_json() -> None:
    snapshot = make_job().snapshot()

    assert json.loads(json.dumps(snapshot_payload(snapshot)))["state"] == "pending"


# --- the first frame ---------------------------------------------------------


def test_the_first_frame_describes_the_present() -> None:
    """A browser connecting late must not stare at an empty page."""
    job = make_job(max_pages=10)
    job.bus.publish(page_crawled())
    job.bus.publish(page_crawled("https://example.test/b"))

    async def first() -> ServerEvent:
        async for event in crawl_events(job, heartbeat=0.05):
            return event
        raise AssertionError("the stream yielded nothing")

    event = run(first)

    assert event.name == "progress"
    assert event.data["pages_visited"] == 2


def test_a_crawl_that_already_finished_says_so_immediately() -> None:
    job = make_job()
    job.bus.publish(
        CrawlFinished(session_id="job-1", state="completed", pages_visited=1, pages_failed=0)
    )

    async def collect() -> list[ServerEvent]:
        return [event async for event in crawl_events(job, heartbeat=0.05)]

    events = run(collect)

    assert [event.name for event in events] == ["progress", "finished"]


def test_a_failed_job_finishes_the_stream_with_its_reason() -> None:
    job = make_job()
    job.fail("HTTP 404 from https://example.test/")

    async def collect() -> list[ServerEvent]:
        return [event async for event in crawl_events(job, heartbeat=0.05)]

    events = run(collect)

    assert events[-1].name == "finished"
    assert "404" in events[-1].data["error"]


# --- live updates ------------------------------------------------------------


def test_progress_from_a_worker_thread_reaches_the_stream() -> None:
    """The whole point of the module: a different thread, a live response."""
    job = make_job(max_pages=10)

    async def collect() -> list[ServerEvent]:
        events: list[ServerEvent] = []
        started = threading.Event()

        def crawl() -> None:
            started.wait(timeout=2)
            job.bus.publish(CrawlStarted(session_id="job-1", seed_url="x", max_depth=1))
            job.bus.publish(page_crawled())
            job.bus.publish(page_crawled("https://example.test/b"))
            job.bus.publish(
                CrawlFinished(
                    session_id="job-1", state="completed", pages_visited=2, pages_failed=0
                )
            )

        worker = threading.Thread(target=crawl, daemon=True)
        worker.start()
        async for event in crawl_events(job, heartbeat=1.0):
            events.append(event)
            started.set()
        worker.join(timeout=2)
        return events

    events = run(collect)

    assert events[0].name == "progress"
    assert events[-1].name == "finished"
    assert events[-1].data["pages_visited"] == 2
    assert events[-1].data["state"] == "completed"


def test_a_slow_reader_gets_the_latest_state_not_a_backlog() -> None:
    """Snapshots coalesce: an old one is worthless once a newer one exists."""
    job = make_job(max_pages=100)

    async def collect() -> list[ServerEvent]:
        events: list[ServerEvent] = []
        async for event in crawl_events(job, heartbeat=0.2):
            events.append(event)
            if len(events) == 1:
                # Everything below happens before the reader asks again.
                for index in range(20):
                    job.bus.publish(page_crawled(f"https://example.test/{index}"))
                job.bus.publish(
                    CrawlFinished(
                        session_id="job-1", state="completed", pages_visited=20, pages_failed=0
                    )
                )
        return events

    events = run(collect)

    assert len(events) < 20
    assert events[-1].name == "finished"
    assert events[-1].data["pages_visited"] == 20


def test_a_quiet_stream_sends_a_heartbeat() -> None:
    job = make_job()

    async def collect() -> list[ServerEvent]:
        events: list[ServerEvent] = []
        async for event in crawl_events(job, heartbeat=0.02):
            events.append(event)
            if len(events) >= 3:
                break
        return events

    events = run(collect)

    assert events[0].name == "progress"
    assert all(event.comment == "ping" for event in events[1:])


def test_the_default_heartbeat_is_short_enough_for_a_proxy() -> None:
    assert 5.0 <= DEFAULT_HEARTBEAT_SECONDS <= 30.0


# --- cleaning up -------------------------------------------------------------


def test_a_finished_stream_leaves_no_listener_behind() -> None:
    job = make_job()
    job.fail("gone")

    async def drain() -> None:
        async for _ in crawl_events(job, heartbeat=0.05):
            pass

    run(drain)

    assert job._listeners == []  # noqa: SLF001


def test_an_abandoned_stream_leaves_no_listener_behind() -> None:
    """A closed tab must not keep a crawl talking to nobody."""
    job = make_job(max_pages=10)

    async def leave_early() -> None:
        async for _ in crawl_events(job, heartbeat=0.05):
            break

    run(leave_early)

    assert job._listeners == []  # noqa: SLF001


def test_a_listener_for_a_loop_that_is_gone_is_not_an_error() -> None:
    """The server shutting down must not raise inside somebody's crawl."""
    loop = asyncio.new_event_loop()
    listener = SnapshotListener(loop)
    loop.close()

    listener.offer(make_job().snapshot())


def test_two_streams_watch_the_same_crawl() -> None:
    job = make_job(max_pages=10)

    async def collect() -> tuple[list[str], list[str]]:
        async def watch() -> list[str]:
            return [
                event.name
                async for event in crawl_events(job, heartbeat=0.5)
                if event.comment is None
            ]

        first = asyncio.create_task(watch())
        second = asyncio.create_task(watch())
        await asyncio.sleep(0.05)
        job.bus.publish(page_crawled())
        job.bus.publish(
            CrawlFinished(session_id="job-1", state="completed", pages_visited=1, pages_failed=0)
        )
        return await first, await second

    left, right = run(collect)

    assert left[-1] == "finished"
    assert right[-1] == "finished"
    assert job._listeners == []  # noqa: SLF001


# --- the rendered stream -----------------------------------------------------


def test_the_rendered_stream_is_text() -> None:
    job = make_job()
    job.fail("gone")

    async def collect() -> list[str]:
        return [frame async for frame in event_stream(job, heartbeat=0.05)]

    frames = run(collect)

    assert frames[0].startswith("event: progress\ndata: {")
    assert frames[-1].startswith("event: finished\n")


def test_a_page_failure_is_counted_in_the_stream() -> None:
    job = make_job(max_pages=10)
    job.bus.publish(
        PageFailed(session_id="job-1", url="https://example.test/x", depth=1, reason="HTTP 404")
    )

    async def first() -> ServerEvent:
        async for event in crawl_events(job, heartbeat=0.05):
            return event
        raise AssertionError("the stream yielded nothing")

    assert run(first).data["pages_failed"] == 1


def test_a_stream_over_a_job_that_never_starts_keeps_breathing() -> None:
    """It must not end on its own, and it must not go silent either."""
    job = make_job()

    async def collect() -> int:
        count = 0
        async for _ in crawl_events(job, heartbeat=0.01):
            count += 1
            if count >= 3:
                break
        return count

    assert run(collect, timeout=2.0) == 3
