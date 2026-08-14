"""What somebody decided about a stored resource.

Beside :class:`~maxicrawler.domain.downloads.DownloadStatus` rather than inside
the library, because it is the same *kind* of word: a small closed vocabulary
that every layer has to be able to say. The library writes it into a document,
the download path reads it to know what not to fetch again, and the interface
turns it into a chip and a button — three layers, and none of them should have
to reach into another one to name a verdict.

What is *not* here is :class:`~maxicrawler.library.records.ReviewRecord`, which
carries the verdict together with timestamps and a star and knows how to write
itself into a metadata document. That is storage, and it stays where the other
records are.
"""

from enum import StrEnum


class ReviewVerdict(StrEnum):
    """What a person decided about a stored resource.

    Deliberately *not* a member of
    :class:`~maxicrawler.domain.downloads.DownloadStatus`. That enum records how
    a transfer ended — something that happened to the resource — and this one
    records what somebody thought of the result. A file can perfectly well have
    arrived completely and be worthless, and one vocabulary covering both would
    have to answer "did this download work?" with "the person did not like it".
    """

    UNREVIEWED = "unreviewed"
    """Nobody has judged this yet. The state every stored resource starts in."""

    KEPT = "kept"
    """Worth having. Nothing follows from it except that it has been looked at."""

    IGNORED = "ignored"
    """Not interesting, but not in the way. The payload stays on disk."""

    DISCARDED = "discarded"
    """Not wanted, and the payload has been removed.

    The record stays behind as a tombstone, and that is the entire point of it:
    it is the only thing that stops the next bulk queue from fetching the file
    again, because "the library holds it" is answered by the record *and* the
    file, and the file is gone.
    """

    @property
    def is_dismissed(self) -> bool:
        """Return whether this verdict means *do not offer this to me again*."""
        return self in {ReviewVerdict.IGNORED, ReviewVerdict.DISCARDED}
