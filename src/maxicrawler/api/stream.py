"""Carrying a crawl's progress from a worker thread to a browser.

A crawl runs on a worker thread and publishes synchronously; a response is an
async generator on the event loop. Everything here exists to cross that one
boundary safely, and :func:`asyncio.loop.call_soon_threadsafe` is the whole
bridge — nothing on the worker thread ever touches asyncio directly.

**Snapshots coalesce; they do not queue.** A listener that cannot keep up gets
the *latest* state rather than a backlog of stale ones, because an old snapshot
has no value once a newer one exists. That removes the usual bounded-queue
question entirely: there is nothing to drop, because there is never more than
one thing waiting.

**A listener is registered before the first snapshot is sent.** The other order
has a hole in it: a crawl that finishes in the gap would leave a browser
watching a stream that never says anything again.

Server-sent events rather than WebSockets, and hand-written rather than through
an htmx extension. ``EventSource`` is a browser standard that will behave the
same in ten years; htmx's SSE support has already broken once between major
versions and now lives in a separately released repository. htmx is used for
the things it is good at — a Stop button, a filter, a sort — and this is not
one of them.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from maxicrawler.api import views
from maxicrawler.api.jobs import CrawlJob, JobSnapshot

DEFAULT_HEARTBEAT_SECONDS = 15.0
"""How long a quiet stream waits before sending a comment.

A crawl can be silent for a while — one slow page is enough — and an idle
connection is what a proxy or a browser eventually decides to close.
"""


@dataclass(frozen=True, slots=True)
class ServerEvent:
    """One frame of a ``text/event-stream`` response."""

    name: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    comment: str | None = None
    """Set instead of *name* for a heartbeat, which carries no data."""

    @classmethod
    def ping(cls) -> "ServerEvent":
        """Return the frame that keeps a quiet connection open."""
        return cls(comment="ping")

    def render(self) -> str:
        """Return this frame as the wire format expects it."""
        if self.comment is not None:
            return f": {self.comment}\n\n"
        payload = json.dumps(self.data, separators=(",", ":"), sort_keys=True)
        return f"event: {self.name}\ndata: {payload}\n\n"


def snapshot_payload(snapshot: JobSnapshot) -> dict[str, Any]:
    """Return exactly what the page shows, ready to be patched into it.

    The *same* function that renders the page on the server, not a parallel
    shape. Sending raw numbers instead would push formatting into the browser —
    a second implementation of "1 min 23 s" living in JavaScript, free to
    disagree with the one a reload produces.
    """
    return views.progress_view(snapshot)


class SnapshotListener:
    """Hands snapshots from a worker thread to one event loop.

    One of these belongs to one response. It holds at most one snapshot: a
    newer one replaces an older one that has not been read yet, which is what
    makes a slow reader harmless.
    """

    __slots__ = ("_latest", "_loop", "_wakeup")

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._latest: JobSnapshot | None = None
        self._wakeup = asyncio.Event()

    def offer(self, snapshot: JobSnapshot) -> None:
        """Accept *snapshot* from whichever thread produced it.

        Called inside the crawl, so it does the least possible: hands the value
        to the loop and returns. A loop that has already closed — the server is
        shutting down, the response is gone — is not an error worth raising
        into somebody's crawl.
        """
        try:
            self._loop.call_soon_threadsafe(self._accept, snapshot)
        except RuntimeError:
            return

    def _accept(self, snapshot: JobSnapshot) -> None:
        """Store the newest snapshot and wake the reader. Runs on the loop."""
        self._latest = snapshot
        self._wakeup.set()

    async def next(self, *, timeout: float) -> JobSnapshot | None:
        """Return the next snapshot, or ``None`` when *timeout* passes first."""
        try:
            await asyncio.wait_for(self._wakeup.wait(), timeout=timeout)
        except TimeoutError:
            return None
        self._wakeup.clear()
        return self._latest


async def crawl_events(
    job: CrawlJob, *, heartbeat: float = DEFAULT_HEARTBEAT_SECONDS
) -> AsyncIterator[ServerEvent]:
    """Yield the progress of *job* until it finishes.

    The first frame always describes the present, so a browser that connects
    late — or reconnects after a reload — sees the crawl as it stands rather
    than an empty page waiting for the next change.
    """
    listener = SnapshotListener(asyncio.get_running_loop())
    job.add_listener(listener.offer)
    try:
        snapshot = job.snapshot()
        yield ServerEvent("progress", snapshot_payload(snapshot))
        if snapshot.is_finished:
            yield ServerEvent("finished", snapshot_payload(snapshot))
            return
        while True:
            latest = await listener.next(timeout=heartbeat)
            if latest is None:
                yield ServerEvent.ping()
                continue
            yield ServerEvent("progress", snapshot_payload(latest))
            if latest.is_finished:
                yield ServerEvent("finished", snapshot_payload(latest))
                return
    finally:
        # A closed tab has to leave nothing behind on the crawl.
        job.remove_listener(listener.offer)


async def event_stream(
    job: CrawlJob, *, heartbeat: float = DEFAULT_HEARTBEAT_SECONDS
) -> AsyncIterator[str]:
    """Yield the rendered frames a streaming response writes out."""
    async for event in crawl_events(job, heartbeat=heartbeat):
        yield event.render()
