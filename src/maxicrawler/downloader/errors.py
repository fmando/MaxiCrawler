"""The error hierarchy of the download layer.

These describe failures of *orchestration*: a source that is neither a link nor
a file, a payload that arrived incomplete, a sink used out of order. What a
provider failed at stays a
:class:`~maxicrawler.providers.errors.ProviderError`, and what the store failed
at stays a :class:`~maxicrawler.library.errors.LibraryError`, so a failed
download always says which layer gave up.
"""


class DownloadError(RuntimeError):
    """Base class for every download orchestration failure."""


class SourceError(DownloadError):
    """Raised when a download source cannot be turned into URLs."""
