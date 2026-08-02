"""The backlog of jobs waiting for a worker.

Only one worker drains this queue today, and that is deliberate: parallel
transfers to the same host are a policy question, not a performance trick. The
queue is nevertheless built for more than one from the start, because the two
things that make concurrency painful to retrofit are both structural rather
than incidental.

The first is mutable state without a lock. Every operation here is guarded, so
adding a second worker changes no invariant.

The second is a worker that owns its work. Jobs are handed out one at a time
through :meth:`DownloadQueue.pop`, so a worker never holds a slice of the
backlog and workers never have to agree on who takes what.

What is left to add is a thread pool around the drain loop in
:mod:`maxicrawler.downloader.manager`, plus whatever ordering guarantee the
report should keep. Nothing in this module has to change.
"""

import heapq
from collections.abc import Iterable, Iterator
from threading import Lock

from maxicrawler.downloader.models import DownloadJob, ResourceIdentity


class DownloadQueue:
    """An ordered, duplicate-free backlog of download jobs.

    Jobs come out by descending priority, and jobs of equal priority in the
    order they went in — so a plan that lists a folder's entries produces them
    in that order rather than in whatever order a heap happens to hold.

    A resource that is asked for twice is queued once. Deduplication is by
    identity rather than by URL, so the same file reached through a link with a
    key and a link without one still queues a single time.
    """

    __slots__ = ("_entries", "_lock", "_sequence", "_seen")

    def __init__(self, jobs: Iterable[DownloadJob] = ()) -> None:
        self._lock = Lock()
        self._entries: list[tuple[int, int, DownloadJob]] = []
        self._seen: set[ResourceIdentity] = set()
        self._sequence = 0
        self.extend(jobs)

    def push(self, job: DownloadJob) -> bool:
        """Add *job* and return whether it was new.

        A job whose resource is already queued — or was queued earlier in this
        queue's life — is dropped and ``False`` is returned.
        """
        with self._lock:
            identity = job.identity
            if identity in self._seen:
                return False
            self._seen.add(identity)
            heapq.heappush(self._entries, (-job.priority, self._sequence, job))
            self._sequence += 1
            return True

    def extend(self, jobs: Iterable[DownloadJob]) -> int:
        """Add every job in *jobs* and return how many were new."""
        return sum(1 for job in jobs if self.push(job))

    def pop(self) -> DownloadJob | None:
        """Return the next job, or ``None`` when the backlog is empty.

        Handing out one job at a time is what lets several workers share a
        queue without coordinating.
        """
        with self._lock:
            if not self._entries:
                return None
            return heapq.heappop(self._entries)[2]

    def drain(self) -> Iterator[DownloadJob]:
        """Yield jobs until the backlog is empty.

        The queue may grow while this runs — a provider that discovers more
        work mid-run simply pushes it — because the emptiness check happens on
        every step rather than once at the start.
        """
        while (job := self.pop()) is not None:
            yield job

    @property
    def pending(self) -> int:
        """Return how many jobs are still waiting."""
        with self._lock:
            return len(self._entries)

    def __len__(self) -> int:
        """Return how many jobs are still waiting."""
        return self.pending

    def __bool__(self) -> bool:
        """Return whether any job is still waiting."""
        return self.pending > 0

    def __contains__(self, job: object) -> bool:
        """Return whether the resource of *job* has been queued."""
        if not isinstance(job, DownloadJob):
            return False
        with self._lock:
            return job.identity in self._seen

    def __repr__(self) -> str:
        """Return a representation naming the backlog size only."""
        return f"{type(self).__name__}(pending={self.pending})"
