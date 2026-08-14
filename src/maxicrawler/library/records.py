"""The metadata document stored beside every downloaded resource.

A library entry is only as useful as the account it keeps of itself. The record
here is that account: it says which provider produced the resource, which link
it came from, when it was found and fetched, what the payload is, and whether
the transfer finished. Everything is JSON, so an entry stays readable with an
ordinary text editor years after the program that wrote it.

Two properties make the format survivable:

* **A schema version.** A document written by a newer MaxiCrawler is refused
  rather than misread, so an old binary can never quietly discard fields it
  does not know about.
* **Unknown members are preserved.** Anything the current version does not
  recognise is kept in :attr:`ResourceRecord.extra` and written back
  unchanged, so a future field survives a round trip through today's code.

These models carry a relative path and therefore live outside
:mod:`maxicrawler.domain`, for the same reason
:class:`~maxicrawler.documents.models.Document` does: storage layout is
infrastructure.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from maxicrawler.domain import Checksum, DownloadStatus, ResourceKind, ResourceRef
from maxicrawler.library.errors import LibraryRecordError

RECORD_SCHEMA = 1
"""Version of the metadata document this release writes.

Deliberately unchanged by the arrival of :attr:`ResourceRecord.review`. A
document whose schema is higher than this one is *refused* rather than read, so
raising the number would make every library written here unreadable to any
release that came before — and an added optional member is exactly the case
:attr:`ResourceRecord.extra` exists for: an older MaxiCrawler carries ``review``
through untouched without ever knowing what it means. The number describes the
shape of the document, and that has not changed.
"""

METADATA_FILENAME = "metadata.json"
"""Name of the metadata document inside an entry directory."""

CONTENT_DIRECTORY = "content"
"""Directory inside an entry that holds the payload."""

_KNOWN_KEYS = frozenset(
    {
        "schema",
        "provider",
        "key",
        "resource_id",
        "parent_id",
        "kind",
        "name",
        "source_url",
        "source_document",
        "status",
        "discovered_at",
        "downloaded_at",
        "attempts",
        "error",
        "content",
        "review",
    }
)
"""Members this release understands; anything else is carried in ``extra``."""


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


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    """What somebody decided about one stored resource, and when.

    Written only by the library service and never by a download: the two touch
    disjoint members of the same document, which is what lets a resource be
    fetched again without losing the judgement passed on it.
    """

    verdict: ReviewVerdict = ReviewVerdict.UNREVIEWED
    favourite: bool = False
    """Marked as worth finding again. A switch, independent of the verdict:
    keeping something and starring it are two different statements."""

    reviewed_at: datetime | None = None
    payload_removed_at: datetime | None = None
    """When the payload was deleted, for a discarded entry.

    Recorded rather than inferred from the file being absent. A missing file is
    also what a disk error looks like, and reading one as a decision would turn
    an accident into a permanent verdict — see :meth:`ResourceRecord.is_complete`,
    which is what makes a damaged library repairable by downloading again.
    """

    def to_document(self) -> dict[str, Any]:
        """Return the serializable description of this judgement."""
        return {
            "verdict": self.verdict.value,
            "favourite": self.favourite,
            "reviewed_at": _write_time(self.reviewed_at),
            "payload_removed_at": _write_time(self.payload_removed_at),
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "ReviewRecord":
        """Return the judgement *document* holds.

        Raises:
            LibraryRecordError: the document is not a readable review record.
        """
        return cls(
            verdict=_read_enum(document, "verdict", ReviewVerdict),
            favourite=_optional_bool(document, "favourite"),
            reviewed_at=_read_time(document, "reviewed_at"),
            payload_removed_at=_read_time(document, "payload_removed_at"),
        )


@dataclass(frozen=True, slots=True)
class ContentRecord:
    """The payload one library entry holds.

    ``path`` is relative to the entry directory and always uses forward
    slashes, so a library copied between platforms describes itself the same
    way on both.
    """

    filename: str
    path: str
    size: int
    checksums: tuple[Checksum, ...] = ()

    def checksum(self, algorithm: str) -> str | None:
        """Return the digest produced by *algorithm*, if one was recorded."""
        for checksum in self.checksums:
            if checksum.algorithm == algorithm:
                return checksum.value
        return None

    def to_document(self) -> dict[str, Any]:
        """Return the serializable description of this payload."""
        return {
            "filename": self.filename,
            "path": self.path,
            "size": self.size,
            "checksums": [
                {"algorithm": checksum.algorithm, "value": checksum.value}
                for checksum in self.checksums
            ],
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "ContentRecord":
        """Return the payload description *document* holds.

        Raises:
            LibraryRecordError: the document is not a readable payload record.
        """
        return cls(
            filename=_require_str(document, "filename"),
            path=_require_str(document, "path"),
            size=_require_int(document, "size"),
            checksums=_read_checksums(document.get("checksums")),
        )


@dataclass(frozen=True, slots=True)
class ResourceRecord:
    """Everything the library knows about one downloaded resource.

    The record describes the resource, never the credential that unlocked it.
    ``source_url`` is :attr:`~maxicrawler.domain.providers.ResourceRef.url`,
    which already has its fragment removed, so a library directory is safe to
    share, back up, or paste into an issue.
    """

    provider: str
    key: str
    resource_id: str
    kind: ResourceKind
    status: DownloadStatus
    source_url: str
    parent_id: str | None = None
    name: str | None = None
    source_document: str | None = None
    discovered_at: datetime | None = None
    downloaded_at: datetime | None = None
    attempts: int = 0
    error: str | None = None
    content: ContentRecord | None = None
    review: ReviewRecord | None = None
    """What somebody decided about this resource, once somebody has.

    ``None`` for everything nobody has judged, which is every entry written
    before this member existed. Absent rather than a default instance, so an
    unreviewed entry's document says ``null`` and a reader can tell "not looked
    at" from "looked at and shrugged".
    """

    extra: Mapping[str, Any] = field(default_factory=dict)
    """Members a future release added, preserved verbatim across a round trip."""

    @property
    def is_complete(self) -> bool:
        """Return whether this record claims a finished, stored payload."""
        return self.status is DownloadStatus.COMPLETED and self.content is not None

    @property
    def verdict(self) -> ReviewVerdict:
        """Return the judgement passed on this resource, unreviewed by default.

        Saves every caller the same ``None`` check, and keeps "nobody has
        looked at this" a value rather than an absence.
        """
        return ReviewVerdict.UNREVIEWED if self.review is None else self.review.verdict

    def to_document(self) -> dict[str, Any]:
        """Return the serializable description of this resource.

        Unknown members are written first, so a recognised field always wins
        over a stale copy of itself that an older document might carry.
        """
        document: dict[str, Any] = {key: value for key, value in self.extra.items()}
        document.update(
            {
                "schema": RECORD_SCHEMA,
                "provider": self.provider,
                "key": self.key,
                "resource_id": self.resource_id,
                "parent_id": self.parent_id,
                "kind": self.kind.value,
                "name": self.name,
                "source_url": self.source_url,
                "source_document": self.source_document,
                "status": self.status.value,
                "discovered_at": _write_time(self.discovered_at),
                "downloaded_at": _write_time(self.downloaded_at),
                "attempts": self.attempts,
                "error": self.error,
                "content": None if self.content is None else self.content.to_document(),
                "review": None if self.review is None else self.review.to_document(),
            }
        )
        return document

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "ResourceRecord":
        """Return the record *document* describes.

        Raises:
            LibraryRecordError: the document is unreadable, or was written by a
                release whose schema this one does not know.
        """
        schema = _require_int(document, "schema")
        if schema > RECORD_SCHEMA:
            msg = (
                f"metadata was written by a newer MaxiCrawler: schema {schema}, "
                f"this release understands {RECORD_SCHEMA}"
            )
            raise LibraryRecordError(msg)
        content = document.get("content")
        review = document.get("review")
        return cls(
            provider=_require_str(document, "provider"),
            key=_require_str(document, "key"),
            resource_id=_require_str(document, "resource_id"),
            kind=_read_enum(document, "kind", ResourceKind),
            status=_read_enum(document, "status", DownloadStatus),
            source_url=_require_str(document, "source_url"),
            parent_id=_optional_str(document, "parent_id"),
            name=_optional_str(document, "name"),
            source_document=_optional_str(document, "source_document"),
            discovered_at=_read_time(document, "discovered_at"),
            downloaded_at=_read_time(document, "downloaded_at"),
            attempts=_optional_int(document, "attempts") or 0,
            error=_optional_str(document, "error"),
            content=None if content is None else ContentRecord.from_document(_mapping(content)),
            review=None if review is None else ReviewRecord.from_document(_mapping(review)),
            extra={key: value for key, value in document.items() if key not in _KNOWN_KEYS},
        )


def new_record(
    ref: ResourceRef,
    key: str,
    *,
    status: DownloadStatus,
    name: str | None = None,
    source_document: str | None = None,
    discovered_at: datetime | None = None,
) -> ResourceRecord:
    """Return the record a freshly seen resource starts out with.

    Building it here rather than in the download manager keeps the mapping from
    a :class:`~maxicrawler.domain.providers.ResourceRef` to a stored document in
    the layer that owns the document.
    """
    return ResourceRecord(
        provider=ref.provider,
        key=key,
        resource_id=ref.resource_id,
        kind=ref.kind,
        status=status,
        source_url=ref.url,
        parent_id=ref.parent_id,
        name=name,
        source_document=source_document,
        discovered_at=discovered_at,
    )


def _mapping(value: object) -> Mapping[str, Any]:
    """Return *value* as a mapping, or report that it is not one."""
    if not isinstance(value, Mapping):
        msg = "metadata member must be an object"
        raise LibraryRecordError(msg)
    return value


def _require_str(document: Mapping[str, Any], key: str) -> str:
    """Return the mandatory string member *key*."""
    value = document.get(key)
    if not isinstance(value, str):
        msg = f"metadata member {key!r} must be a string"
        raise LibraryRecordError(msg)
    return value


def _optional_str(document: Mapping[str, Any], key: str) -> str | None:
    """Return the optional string member *key*."""
    value = document.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"metadata member {key!r} must be a string or null"
        raise LibraryRecordError(msg)
    return value


def _require_int(document: Mapping[str, Any], key: str) -> int:
    """Return the mandatory integer member *key*."""
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"metadata member {key!r} must be an integer"
        raise LibraryRecordError(msg)
    return value


def _optional_int(document: Mapping[str, Any], key: str) -> int | None:
    """Return the optional integer member *key*."""
    value = document.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"metadata member {key!r} must be an integer or null"
        raise LibraryRecordError(msg)
    return value


def _optional_bool(document: Mapping[str, Any], key: str) -> bool:
    """Return the optional boolean member *key*, absent meaning false.

    An absent switch is off rather than an error: a review record written before
    a switch existed is a document this release still has to read.
    """
    value = document.get(key)
    if value is None:
        return False
    if not isinstance(value, bool):
        msg = f"metadata member {key!r} must be true or false"
        raise LibraryRecordError(msg)
    return value


def _read_enum[EnumT: (ResourceKind, DownloadStatus, ReviewVerdict)](
    document: Mapping[str, Any], key: str, enum: type[EnumT]
) -> EnumT:
    """Return the member *key* as a value of *enum*."""
    try:
        return enum(_require_str(document, key))
    except ValueError as error:
        msg = f"metadata member {key!r} is not a known {enum.__name__}"
        raise LibraryRecordError(msg) from error


def _read_time(document: Mapping[str, Any], key: str) -> datetime | None:
    """Return the optional ISO-8601 timestamp member *key*."""
    value = _optional_str(document, key)
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        msg = f"metadata member {key!r} is not an ISO-8601 timestamp"
        raise LibraryRecordError(msg) from error


def _write_time(value: datetime | None) -> str | None:
    """Return *value* as an ISO-8601 string, or ``None``."""
    return None if value is None else value.isoformat()


def _read_checksums(value: object) -> tuple[Checksum, ...]:
    """Return the recorded digests, tolerating an absent list."""
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        msg = "metadata member 'checksums' must be an array"
        raise LibraryRecordError(msg)
    try:
        return tuple(
            Checksum(
                algorithm=_require_str(_mapping(entry), "algorithm"),
                value=_require_str(_mapping(entry), "value"),
            )
            for entry in value
        )
    except ValueError as error:
        msg = f"metadata member 'checksums' holds an unusable digest: {error}"
        raise LibraryRecordError(msg) from error
