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

    REFUSED = "refused"
    """Nothing was kept, because a rule here declined it.

    Distinct from :attr:`SKIPPED`, and the distinction is not pedantry: skipped
    means *the payload is present already*, so it counts as a success and is
    shown as "already stored". A refusal leaves no payload at all, and calling
    it either of those would be a counter and a label that both lie.

    Distinct from :attr:`FAILED` too. Nothing went wrong: a limit somebody
    configured did exactly what it was configured to do, and a page reporting a
    fault would send them looking for one.

    The reason always names the rule and the numbers, because a file that
    vanished without one is the failure this whole state exists to prevent.
    """

    FAILED = "failed"
    """The transfer was attempted and did not finish."""

    CANCELLED = "cancelled"
    """Somebody asked for it to stop, and it did.

    Not a failure: nothing went wrong, and a page that reported one would be
    telling a person their own decision was an error.

    Never written to a metadata document. A cancelled transfer leaves the
    library exactly as it was — no partial file, and no record claiming an
    attempt that nobody made — so this value lives only in an outcome a client
    is being shown right now.
    """

    @property
    def is_final(self) -> bool:
        """Return whether no further work is expected for this status."""
        return self in {
            DownloadStatus.COMPLETED,
            DownloadStatus.SKIPPED,
            DownloadStatus.REFUSED,
            DownloadStatus.FAILED,
            DownloadStatus.CANCELLED,
        }

    @property
    def is_success(self) -> bool:
        """Return whether the library holds the payload afterwards.

        A skipped download is a success: the resource is present, it simply
        did not have to be fetched again.
        """
        return self in {DownloadStatus.COMPLETED, DownloadStatus.SKIPPED}

    @property
    def invites_retry(self) -> bool:
        """Return whether asking for this again could end differently.

        Not the same question as "did this succeed?", although the two agreed
        until :attr:`REFUSED` existed. A dead share might come back, a broken
        transfer might complete, and a stop somebody has thought better of is
        one click from being undone — so all three are worth offering again.

        A refusal is not: the rule that turned it away is configuration, and it
        will turn it away identically on every attempt. Offering the button
        anyway would be a control that cannot work, which is the one thing
        ADR-038 says an interface must not render.
        """
        return self in {DownloadStatus.FAILED, DownloadStatus.CANCELLED}


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
