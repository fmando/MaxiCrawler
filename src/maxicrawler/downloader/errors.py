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


class DownloadRefusedError(DownloadError):
    """Raised inside a transfer a rule here declined to keep.

    A :class:`DownloadError` for the reason
    :class:`DownloadCancelledError` is: every provider already lets one out of
    ``sink.begin`` and ``sink.write``, so a refusal travels a path each of them
    was written to survive, and no provider had to learn a second one — or,
    more to the point, had to learn that there is a rule at all.

    What separates it from a failure is again what the *manager* does with it.
    Nothing broke: a limit somebody configured turned a payload away, the
    library is left exactly as it was, and the record says so with the numbers
    that decided it.
    """


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
