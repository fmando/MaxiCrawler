"""The persistence port used by the discovery layer.

The protocol is declared here, next to its consumer, rather than in the
``database`` package. The discovery layer therefore depends on an abstraction
it owns and never on a storage implementation; adapters satisfy the protocol
structurally and are wired in by the composition root.
"""

from typing import Protocol, runtime_checkable

from maxicrawler.domain import DiscoveryResult, ScanSession, Statistics


@runtime_checkable
class DiscoveryRepository(Protocol):
    """Persists the outcome of a discovery session.

    Implementations must be safe to call in the order ``start_session``,
    any number of ``save_result`` calls, then ``finish_session``.
    """

    def start_session(self, session: ScanSession) -> None:
        """Record the beginning of *session*."""
        ...

    def save_result(self, session: ScanSession, result: DiscoveryResult) -> None:
        """Persist one discovery result belonging to *session*."""
        ...

    def finish_session(self, session: ScanSession, statistics: Statistics) -> None:
        """Record the completion of *session* together with its counters."""
        ...


class NullDiscoveryRepository:
    """A repository that discards everything.

    It is the default for callers that want discovery without persistence,
    and it keeps the application service testable without a database.
    """

    def start_session(self, session: ScanSession) -> None:
        """Do nothing."""

    def save_result(self, session: ScanSession, result: DiscoveryResult) -> None:
        """Do nothing."""

    def finish_session(self, session: ScanSession, statistics: Statistics) -> None:
        """Do nothing."""
