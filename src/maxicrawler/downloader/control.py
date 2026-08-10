"""The button that stops a transfer.

A crawl has had one since Sprint 9: :class:`~maxicrawler.web.session.CrawlControl`
is an :class:`~threading.Event` a caller holds while the engine checks it
between pages. A transfer had no such seam. One page takes a second; one file
can take an hour, so *"stop"* meant *"wait for it"* — and a server shutting
down left a transfer running until the file was done.

This is the same object for the other half of the chain, deliberately so: two
background things in one server should not have two designs (ADR-024).

**Where it is checked is what makes it work.** Not in the manager, which is
between jobs and would only stop the *next* file; not in the provider, which
would mean every provider implementing cancellation and one of them forgetting.
It is checked in :class:`~maxicrawler.downloader.sink.LibrarySink` on the way
in, once per chunk — the one place every provider's bytes already pass through,
and the place that already guarantees an unfinished transfer leaves nothing
behind.

So a cancelled download is not a special path: it raises where a broken
connection would raise, the staging file is discarded exactly as it is for any
other unfinished transfer, and the library is left as it was.
"""

from threading import Event


class DownloadControl:
    """Asks a running transfer to stop, from another thread.

    Deliberately as small as :class:`~maxicrawler.web.session.CrawlControl`, and
    without its state field: a download's state is already reported by
    :class:`~maxicrawler.domain.DownloadStatus` on the outcome, and a second
    place to read it would eventually disagree with the first.
    """

    __slots__ = ("_stop",)

    def __init__(self) -> None:
        self._stop = Event()

    def request_stop(self) -> None:
        """Ask the transfer to stop at the next chunk it is handed."""
        self._stop.set()

    @property
    def stop_requested(self) -> bool:
        """Return whether a stop has been asked for."""
        return self._stop.is_set()

    def wait(self, seconds: float) -> None:
        """Block for up to *seconds*, returning at once when a stop is asked for."""
        self._stop.wait(seconds)

    def __repr__(self) -> str:
        """Return a representation naming the stop flag."""
        return f"{type(self).__name__}(stop_requested={self.stop_requested})"
