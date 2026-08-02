"""Immutable domain models describing providers and the resources they expose.

The plugin vocabulary in :mod:`maxicrawler.domain.plugins` answers *"can I
classify this URL?"*. The vocabulary here answers the next question, *"what can
I do with this resource?"*, and is deliberately free of any provider knowledge:
a Mega share, a Pixeldrain file, and a GoFile folder are all described with the
same value objects.

Like the rest of the domain, this module depends on nothing but the standard
library and its sibling domain modules.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from maxicrawler.domain.plugins import LinkAttribute


class ProviderCapability(StrEnum):
    """A coarse capability a provider advertises to the application layer."""

    INSPECT = "inspect"
    """Reads metadata about a single resource."""

    LIST = "list"
    """Enumerates the entries a container resource holds."""

    DOWNLOAD = "download"
    """Transfers resource content.

    Advertised only when the provider was actually composed with everything a
    transfer needs, so a caller can ask before it tries.
    """


class ResourceKind(StrEnum):
    """What a referenced resource turned out to be."""

    FILE = "file"
    FOLDER = "folder"
    UNKNOWN = "unknown"
    """The link identifies a resource without stating its kind."""


class Availability(StrEnum):
    """Whether a resource can still be reached, and why not when it cannot.

    A resource that was deleted, revoked, or taken down is a legitimate answer
    to *"what can I do with this?"* rather than a failure, so these outcomes are
    reported as values instead of raised as exceptions. Only faults on our side
    — a broken connection, an unparsable response — raise.
    """

    AVAILABLE = "available"
    NOT_FOUND = "not_found"
    """The resource no longer exists."""

    ACCESS_DENIED = "access_denied"
    """The share was revoked, or the link is incomplete."""

    BLOCKED = "blocked"
    """The provider removed the resource administratively."""

    RATE_LIMITED = "rate_limited"
    """The provider refused to answer for now; the resource may still exist."""

    QUOTA_EXCEEDED = "quota_exceeded"
    """A transfer or storage quota is exhausted."""

    UNKNOWN = "unknown"
    """No statement could be obtained, for instance in offline mode."""

    @property
    def is_available(self) -> bool:
        """Return whether the resource is reachable right now."""
        return self is Availability.AVAILABLE

    @property
    def is_determined(self) -> bool:
        """Return whether the provider made a statement about the resource.

        Rate limiting, quota exhaustion, and offline inspection leave the
        question open: the resource may well be fine.
        """
        return self not in {
            Availability.RATE_LIMITED,
            Availability.QUOTA_EXCEEDED,
            Availability.UNKNOWN,
        }


class ResourceSecret:
    """A credential unlocking a resource, kept out of every rendering.

    A Mega share link carries its decryption key in the URL fragment, which no
    HTTP client ever transmits. Wrapping the key preserves that property inside
    the process: the value is reachable only through :meth:`reveal`, so it
    cannot slip into a log record, an exception message, a database row, or a
    serialized payload by accident. Every call to :meth:`reveal` is therefore a
    deliberate, greppable decision, and the type is immutable so a wrapped
    secret cannot be swapped underneath a holder.
    """

    __slots__ = ("_value",)

    _value: str
    """Declared for type checkers; ``__slots__`` owns the storage."""

    def __init__(self, value: str) -> None:
        if not value:
            msg = "secret must not be empty"
            raise ValueError(msg)
        object.__setattr__(self, "_value", value)

    def reveal(self) -> str:
        """Return the wrapped value.

        This is the only way to read the secret; call it as late as possible
        and never store the result.
        """
        return self._value

    def __setattr__(self, name: str, value: object) -> None:
        """Reject every mutation attempt."""
        msg = f"{type(self).__name__} is immutable"
        raise AttributeError(msg)

    def __delattr__(self, name: str) -> None:
        """Reject every deletion attempt."""
        msg = f"{type(self).__name__} is immutable"
        raise AttributeError(msg)

    def __repr__(self) -> str:
        """Return a redacted representation; the value never appears."""
        return f"{type(self).__name__}(<redacted>)"

    def __str__(self) -> str:
        """Return a redacted representation; the value never appears."""
        return "<redacted>"

    def __eq__(self, other: object) -> bool:
        """Compare by wrapped value, so equal secrets are interchangeable."""
        if not isinstance(other, ResourceSecret):
            return NotImplemented
        return self._value == other._value

    def __hash__(self) -> int:
        """Hash by wrapped value, keeping the type usable as a mapping key."""
        return hash(self._value)


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    """Describes a provider without coupling callers to its implementation.

    ``priority`` orders providers during resolution: higher values are asked
    first, mirroring :class:`~maxicrawler.domain.plugins.PluginInfo`.
    """

    name: str
    version: str
    module: str
    description: str = ""
    display_name: str = ""
    priority: int = 0
    capabilities: frozenset[ProviderCapability] = frozenset()

    @property
    def label(self) -> str:
        """Return the name to show a human, falling back to a titled ``name``."""
        return self.display_name or self.name.title()

    def supports(self, capability: ProviderCapability) -> bool:
        """Return whether the provider advertises *capability*."""
        return capability in self.capabilities


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """An addressable resource, ready to be inspected.

    A reference is built from a URL without any I/O, so it can be created,
    stored, and compared offline. ``url`` is the share URL **with any
    secret-bearing fragment removed**: the credential lives in ``secret``
    alone, which keeps a plain ``repr()`` of a reference safe to log.

    ``parent_id`` names the container a resource is addressed inside — for a
    Mega link that selects one entry of a shared folder, it holds the folder
    handle while ``resource_id`` holds the entry.
    """

    provider: str
    resource_id: str
    kind: ResourceKind
    url: str
    secret: ResourceSecret | None = None
    parent_id: str | None = None

    @property
    def has_secret(self) -> bool:
        """Return whether a credential travelled with the link."""
        return self.secret is not None

    @property
    def is_contained(self) -> bool:
        """Return whether the resource is addressed inside a container."""
        return self.parent_id is not None


@dataclass(frozen=True, slots=True)
class ResourceMetadata:
    """What a provider could learn about one resource without downloading it.

    Every field except ``kind`` is optional: a provider reports what the
    resource actually disclosed. ``name`` stays ``None`` when the metadata is
    readable but the name is not, which is the normal outcome for an
    end-to-end encrypted share published without its key.
    """

    kind: ResourceKind
    name: str | None = None
    size: int | None = None
    modified_at: datetime | None = None
    attributes: tuple[LinkAttribute, ...] = ()

    def attribute(self, name: str) -> str | None:
        """Return the value of the attribute called *name*, if it is present."""
        for attribute in self.attributes:
            if attribute.name == name:
                return attribute.value
        return None


@dataclass(frozen=True, slots=True)
class ResourceEntry:
    """One resource found inside a container."""

    ref: ResourceRef
    metadata: ResourceMetadata


@dataclass(frozen=True, slots=True)
class ResourceInspection:
    """Everything one inspection established about a resource.

    ``metadata`` is ``None`` when the resource could not be reached at all;
    ``availability`` then says why. ``entries`` is populated for containers and
    stays empty for files.
    """

    ref: ResourceRef
    availability: Availability
    metadata: ResourceMetadata | None = None
    entries: tuple[ResourceEntry, ...] = ()
    names_available: bool = True
    """``False`` when names stayed encrypted because no credential was usable."""

    truncated: bool = False
    """``True`` when the container held more entries than were collected."""

    @property
    def kind(self) -> ResourceKind:
        """Return the resource kind, preferring what the inspection observed."""
        if self.metadata is not None and self.metadata.kind is not ResourceKind.UNKNOWN:
            return self.metadata.kind
        return self.ref.kind

    @property
    def file_count(self) -> int:
        """Return how many files the container holds."""
        return sum(1 for entry in self.entries if entry.metadata.kind is ResourceKind.FILE)

    @property
    def folder_count(self) -> int:
        """Return how many sub-folders the container holds."""
        return sum(1 for entry in self.entries if entry.metadata.kind is ResourceKind.FOLDER)

    @property
    def total_size(self) -> int | None:
        """Return the total number of bytes, or ``None`` if any part is unknown.

        For a container this sums the contained files; for a single file it is
        that file's size.
        """
        if not self.entries:
            return None if self.metadata is None else self.metadata.size
        sizes = [
            entry.metadata.size
            for entry in self.entries
            if entry.metadata.kind is ResourceKind.FILE
        ]
        if any(size is None for size in sizes):
            return None
        return sum(size for size in sizes if size is not None)
