"""Immutable value objects describing a parsed Mega share link."""

from dataclasses import dataclass
from enum import StrEnum


class MegaLinkKind(StrEnum):
    """What a Mega link points at."""

    FILE = "file"
    FOLDER = "folder"


class MegaLinkFormat(StrEnum):
    """Which generation of the Mega URL scheme a link uses."""

    MODERN = "modern"
    """``/file/<handle>#<key>`` — identity lives in the path."""

    LEGACY = "legacy"
    """``#!<handle>!<key>`` — identity lives entirely in the fragment."""


@dataclass(frozen=True, slots=True)
class MegaLink:
    """A recognized Mega share link.

    ``key`` is the decryption key when the link carries one; a share can be
    published without it. ``node_handle`` identifies a single entry selected
    inside a folder share, and ``node_kind`` says what that entry is when the
    URL states it — the legacy format does not.
    """

    kind: MegaLinkKind
    link_format: MegaLinkFormat
    handle: str
    key: str | None = None
    node_handle: str | None = None
    node_kind: MegaLinkKind | None = None

    @property
    def has_key(self) -> bool:
        """Return whether a decryption key was present in the URL."""
        return self.key is not None

    @property
    def selects_node(self) -> bool:
        """Return whether the link points at one entry inside a folder."""
        return self.node_handle is not None
