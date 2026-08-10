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


class DownloadCancelledError(DownloadError):
    """Raised inside a transfer that was asked to stop.

    A :class:`DownloadError` on purpose. Every provider already lets one out of
    ``sink.write`` — that is how a full disk ends a transfer — so cancellation
    travels the path each of them was already written to survive, and no
    provider had to learn a second one.

    What separates it from a failure is what the *manager* does with it: a
    cancelled transfer is not recorded as an attempt that failed, because
    nobody attempted anything. It was called off.
    """
