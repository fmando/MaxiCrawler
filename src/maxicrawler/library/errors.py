"""The error hierarchy of the library layer.

A library failure always means *the store could not be read or written*. What
a download itself did is reported through
:class:`~maxicrawler.domain.downloads.DownloadStatus` instead, so a dead link
never surfaces as a storage error.
"""


class LibraryError(RuntimeError):
    """Base class for every library failure."""


class LibraryRecordError(LibraryError):
    """Raised when a stored metadata document cannot be read.

    The library refuses to guess at a document it does not understand: a
    truncated, hand-edited, or newer-schema record is reported rather than
    silently replaced, because overwriting it would destroy the only account of
    what an entry holds.
    """


class LibraryLayoutError(LibraryError):
    """Raised when a name cannot be placed inside the library layout."""
