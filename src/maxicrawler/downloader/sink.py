"""Where a provider's bytes actually land.

This is the download manager's half of the
:class:`~maxicrawler.providers.protocol.DownloadSink` contract, and the only
place where the two layers touch the same bytes. Everything a provider must not
have to think about lives here: the staging file, the digest, the byte count,
the progress callback, and the rule that a payload becomes visible only once it
is whole.

The digest is computed while writing rather than by re-reading the file
afterwards. It costs nothing extra on a stream that is being written anyway,
and it means a stored resource always carries a checksum without a second pass
over a file that may be gigabytes long.
"""

import hashlib
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

from maxicrawler.domain import Checksum, ContentDescriptor
from maxicrawler.downloader.control import DownloadControl
from maxicrawler.downloader.errors import DownloadCancelledError, DownloadError
from maxicrawler.library import ContentRecord, LibraryEntry, safe_filename

DEFAULT_HASH_ALGORITHM = "sha256"
"""Digest recorded for every stored payload.

Provider-independent on purpose: a host that offers its own integrity check
adds to this rather than replacing it, so two resources from different
providers stay comparable.
"""

ProgressCallback = Callable[[int], None]
"""Called with the total number of bytes written so far."""


class LibrarySink:
    """Writes one provider's content into one library entry.

    The sink is a context manager, and that is the safety property: leaving the
    block without a successful :meth:`commit` discards the staged file. A
    transfer that fails, is interrupted, or arrives short therefore leaves the
    library exactly as it was, rather than leaving a partial file that a later
    run would mistake for a finished download.
    """

    __slots__ = (
        "_algorithm",
        "_committed",
        "_control",
        "_descriptor",
        "_digest",
        "_entry",
        "_filename",
        "_handle",
        "_on_progress",
        "_staged",
        "_written",
    )

    def __init__(
        self,
        entry: LibraryEntry,
        *,
        algorithm: str = DEFAULT_HASH_ALGORITHM,
        on_progress: ProgressCallback | None = None,
        control: DownloadControl | None = None,
    ) -> None:
        self._entry = entry
        self._algorithm = algorithm
        self._on_progress = on_progress
        self._control = control
        self._digest = hashlib.new(algorithm)
        self._descriptor: ContentDescriptor | None = None
        self._handle: BinaryIO | None = None
        self._staged: Path | None = None
        self._filename = ""
        self._written = 0
        self._committed = False

    @property
    def filename(self) -> str:
        """Return the name the payload will be stored under."""
        return self._filename

    @property
    def bytes_written(self) -> int:
        """Return how many bytes have arrived so far."""
        return self._written

    @property
    def expected_size(self) -> int | None:
        """Return the size the provider announced, if it announced one."""
        return None if self._descriptor is None else self._descriptor.size

    @property
    def checksums(self) -> tuple[Checksum, ...]:
        """Return the digest of everything written so far."""
        return (Checksum(self._algorithm, self._digest.hexdigest()),)

    def begin(self, content: ContentDescriptor) -> None:
        """Open the staging file for the payload *content* describes.

        Raises:
            DownloadError: the transfer already began, or the staging file
                could not be opened.
        """
        if self._handle is not None:
            msg = "a transfer announced its content twice"
            raise DownloadError(msg)
        self._descriptor = content
        self._filename = safe_filename(content.name)
        self._staged = self._entry.reserve(self._filename)
        try:
            self._handle = self._staged.open("wb")
        except OSError as error:
            msg = f"payload could not be opened for writing: {self._staged}"
            raise DownloadError(msg) from error

    def write(self, chunk: bytes) -> None:
        """Append *chunk*, updating the digest, the count, and the progress.

        Also where a stop takes effect, and the only place it could: this is
        the one point every provider's bytes pass through, so cancellation is
        one check here rather than a feature each provider has to remember.
        It is checked *before* the chunk is written, so a stopped transfer does
        no more work than it had already begun.

        Raises:
            DownloadCancelledError: somebody asked the transfer to stop. The
                staging file is discarded by the context manager, exactly as it
                is for a transfer that broke.
            DownloadError: nothing announced this content, or the write failed.
        """
        if self._control is not None and self._control.stop_requested:
            msg = f"stopped after {self._written} bytes"
            raise DownloadCancelledError(msg)
        if self._handle is None:
            msg = "content was written before the transfer announced it"
            raise DownloadError(msg)
        try:
            self._handle.write(chunk)
        except OSError as error:
            msg = f"payload could not be written: {self._staged}"
            raise DownloadError(msg) from error
        self._digest.update(chunk)
        self._written += len(chunk)
        if self._on_progress is not None:
            self._on_progress(self._written)

    def commit(self) -> ContentRecord:
        """Move the finished payload into place and describe what was stored.

        A payload that is shorter or longer than the provider announced is
        discarded rather than stored. Storing it would produce an entry that
        claims to be complete while holding a truncated file, which is the one
        failure a library must never contain.

        The staging directory is removed afterwards, so a finished entry
        contains only what it holds — an empty ``.incomplete`` left behind
        would read as a transfer that never finished.

        Raises:
            DownloadError: nothing was announced, or the payload is not the
                size it was supposed to be.
        """
        if self._handle is None or self._staged is None:
            msg = "the transfer announced no content"
            raise DownloadError(msg)
        expected = self.expected_size
        if expected is not None and expected != self._written:
            self.abort()
            msg = f"transfer is incomplete: {self._written} of {expected} bytes arrived"
            raise DownloadError(msg)
        self._close()
        stored = self._entry.commit(self._staged, self._filename)
        self._committed = True
        self._filename = stored.name
        self._entry.discard()
        return ContentRecord(
            filename=stored.name,
            path=stored.relative_to(self._entry.path).as_posix(),
            size=self._written,
            checksums=self.checksums,
        )

    def abort(self) -> None:
        """Close the staging file and remove it."""
        self._close()
        self._entry.discard()

    def __enter__(self) -> "LibrarySink":
        """Return the sink itself, so it can be used in a ``with`` block."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Discard anything that was never committed."""
        if not self._committed:
            self.abort()

    def _close(self) -> None:
        """Close the staging file if it is open."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None
