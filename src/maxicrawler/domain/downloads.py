"""Immutable domain models describing a transfer and what it produced.

The provider vocabulary in :mod:`maxicrawler.domain.providers` answers *"what
can I do with this resource?"*. The vocabulary here answers the next question,
*"what happened when we fetched it?"*, and is deliberately free of any provider
and any storage knowledge: a Mega file, a Pixeldrain file, and a GoFile entry
all report their transfer with the same value objects.

Like the rest of the domain, this module depends on nothing but the standard
library and its sibling domain modules. In particular it carries no
:class:`~pathlib.Path`: where a payload ended up is a question for
:mod:`maxicrawler.library`, not for the domain.
"""

from dataclasses import dataclass
from enum import StrEnum


class DownloadStatus(StrEnum):
    """Where one requested transfer stands.

    The states are deliberately few. Everything a download can do is either
    still ahead of it, happening, or one of three verdicts, and a stored
    metadata document records exactly one of them.
    """

    PENDING = "pending"
    """Queued, not started."""

    RUNNING = "running"
    """A worker is transferring it right now."""

    COMPLETED = "completed"
    """The payload arrived in full and is stored."""

    SKIPPED = "skipped"
    """Nothing was transferred because the library already holds it."""

    FAILED = "failed"
    """The transfer was attempted and did not finish."""

    @property
    def is_final(self) -> bool:
        """Return whether no further work is expected for this status."""
        return self in {
            DownloadStatus.COMPLETED,
            DownloadStatus.SKIPPED,
            DownloadStatus.FAILED,
        }

    @property
    def is_success(self) -> bool:
        """Return whether the library holds the payload afterwards.

        A skipped download is a success: the resource is present, it simply
        did not have to be fetched again.
        """
        return self in {DownloadStatus.COMPLETED, DownloadStatus.SKIPPED}


@dataclass(frozen=True, slots=True)
class Checksum:
    """A digest of a payload, named by the algorithm that produced it.

    Several checksums can describe the same payload, which is why they are
    kept as a list of named values rather than as one field per algorithm.
    """

    algorithm: str
    value: str

    def __post_init__(self) -> None:
        if not self.algorithm.strip():
            msg = "checksum algorithm must not be empty"
            raise ValueError(msg)
        if not self.value.strip():
            msg = "checksum value must not be empty"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ContentDescriptor:
    """What a provider knows about a payload as it starts transferring it.

    Both fields are optional because a provider reports what the resource
    actually disclosed: an end-to-end encrypted share published without its key
    has no readable name, and a host that answers without a length has no size
    to give. A consumer treats ``None`` as *unknown*, never as *zero*.
    """

    name: str | None = None
    size: int | None = None

    def __post_init__(self) -> None:
        if self.size is not None and self.size < 0:
            msg = "content size must not be negative"
            raise ValueError(msg)

    @property
    def has_size(self) -> bool:
        """Return whether the provider stated how large the payload is."""
        return self.size is not None
