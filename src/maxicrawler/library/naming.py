"""How a resource and its payload are named on disk.

Every name the library writes comes from somewhere else — a provider registry,
a share handle, an attribute block decrypted from a remote host — and none of
those sources is bound by the rules of a file system. This module is the single
place where such a name is turned into a path component, so the guarantees hold
everywhere:

* a component never escapes its directory, whatever the input contained;
* two distinct resources never collide, not even on a case-insensitive volume;
* a component is legal on Windows, macOS, and Linux alike.

Nothing here touches the file system. The functions are pure, so the layout of
a library can be predicted, asserted, and reasoned about without creating one.
"""

import re
from hashlib import sha256
from pathlib import PurePosixPath, PureWindowsPath

from maxicrawler.domain import ResourceRef
from maxicrawler.library.errors import LibraryLayoutError

KEY_DIGEST_LENGTH = 10
"""Hex characters of the digest that disambiguates a resource key.

Ten hex characters are forty bits. A library would need on the order of a
million entries *per provider* before a collision became likely, and a
collision is caught rather than silently accepted, so the short form is worth
the readability it buys.
"""

MAX_SLUG_LENGTH = 24
"""How much of a resource identifier survives into its directory name."""

MAX_FILENAME_LENGTH = 120
"""Longest payload file name the library writes.

Windows caps a whole path at 260 characters by default. Leaving the bulk of
that budget to the library root keeps a deeply nested root usable.
"""

FALLBACK_FILENAME = "content.bin"
"""Payload name used when a provider disclosed none."""

_SLUG_ALLOWED = re.compile(r"[^a-z0-9]+")
"""Everything a slug is reduced to lower-case alphanumerics from."""

_PATH_COMPONENT = re.compile(r"^[a-z0-9][a-z0-9-]*$")
"""What this module is willing to recognise as one of its own components.

Deliberately narrower than what a file system would accept. Everything
:func:`provider_directory` and :func:`resource_key` produce matches it, and
almost nothing else does: no dot, so ``.`` and ``..`` are out; no separator, so
nothing traverses; no upper case, so two spellings cannot address one directory
on a case-insensitive volume; nothing outside ASCII, so no lookalike character
can stand in for another.
"""

MAX_PATH_COMPONENT_LENGTH = 64
"""Longest component this module recognises.

A provider directory is at most 24 characters and a resource key at most 35, so
the limit is slack rather than a constraint — it is here to bound what an
untrusted caller can hand over at all.
"""

_FILENAME_FORBIDDEN = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
"""Control characters and the characters Windows reserves in a file name."""

_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{digit}" for digit in "123456789"}
    | {f"lpt{digit}" for digit in "123456789"}
)
"""Device names Windows refuses as a file name, with or without a suffix."""


def resource_key(ref: ResourceRef) -> str:
    """Return the directory name identifying *ref* inside its provider.

    The key is ``<slug>-<digest>``: a readable stem taken from the resource
    identifier, and a digest over the full identity that makes the result
    unambiguous. Both halves are needed.

    The slug alone would not do. A Mega handle is case-sensitive base64url, so
    ``AbCdEfGh`` and ``abcdefgh`` are different resources that a
    case-insensitive volume — the default on Windows and macOS — would map onto
    one directory, silently merging two downloads.

    The digest alone would do, but nobody could read the result. Keeping the
    stem means ``ls`` on a provider directory still says something.

    The key is stable: it is derived from the reference alone, never from a
    name, a size, or a timestamp, so an entry keeps its place when the resource
    behind it is renamed or re-inspected.
    """
    slug = _slug(ref.resource_id, MAX_SLUG_LENGTH)
    digest = _digest(ref)
    return f"{slug}-{digest}" if slug else digest


def provider_directory(name: str) -> str:
    """Return the directory name a provider's entries live under.

    A provider name comes from a registry that third-party code can add to, so
    it is reduced to the same safe alphabet as a resource key rather than
    trusted verbatim.

    Raises:
        LibraryLayoutError: *name* contains nothing usable.
    """
    slug = _slug(name, MAX_SLUG_LENGTH)
    if not slug:
        msg = f"provider name yields no usable directory: {name!r}"
        raise LibraryLayoutError(msg)
    return slug


def is_path_component(value: str) -> bool:
    """Return whether *value* is a component this module could have produced.

    The inverse direction of the rest of this module. :func:`resource_key` and
    :func:`provider_directory` turn something untrusted into a path component;
    this decides whether an incoming string is one — which is the question a
    caller has when a component arrives from outside, in a URL for instance.

    Answering it by pattern rather than by inspecting the file system is the
    point: a component is rejected because of what it *is*, before anything is
    joined onto a path, and the answer cannot depend on what happens to exist.
    """
    return len(value) <= MAX_PATH_COMPONENT_LENGTH and _PATH_COMPONENT.match(value) is not None


def safe_filename(name: str | None, *, fallback: str = FALLBACK_FILENAME) -> str:
    """Return a payload file name that is safe to join onto a directory.

    *name* arrives from a remote host and is treated as hostile. Any directory
    component is discarded — both POSIX and Windows separators, so a name is
    stripped the same way regardless of the platform reading it — leaving a
    single path component that cannot traverse anywhere. Reserved characters
    become underscores, trailing dots and spaces are removed because Windows
    drops them silently, and a reserved device name is prefixed.

    The extension is preserved when the name has to be shortened, so a
    truncated file still opens with the right application.
    """
    candidate = _last_component(name or "")
    candidate = _FILENAME_FORBIDDEN.sub("_", candidate).strip().rstrip(". ")
    if not candidate or candidate in {".", ".."}:
        return fallback
    if PurePosixPath(candidate).stem.casefold() in _WINDOWS_RESERVED:
        candidate = f"_{candidate}"
    return _truncate(candidate, MAX_FILENAME_LENGTH)


def _last_component(name: str) -> str:
    """Return *name* with every directory component removed.

    Both path flavours are applied, because a name produced on one platform is
    read on another: ``..\\..\\secrets`` must lose its prefix on Linux too.
    """
    stripped = PureWindowsPath(PurePosixPath(name.strip()).name).name
    return stripped.strip()


def _truncate(name: str, limit: int) -> str:
    """Return *name* shortened to *limit* characters, keeping its suffix."""
    if len(name) <= limit:
        return name
    suffix = PurePosixPath(name).suffix[:16]
    stem = name[: limit - len(suffix)].rstrip(". ")
    return f"{stem}{suffix}" if stem else name[:limit]


def _slug(value: str, limit: int) -> str:
    """Return *value* reduced to lower-case alphanumerics, at most *limit* long."""
    return _SLUG_ALLOWED.sub("", value.casefold())[:limit]


def _digest(ref: ResourceRef) -> str:
    """Return the short digest over everything that identifies *ref*.

    The provider, the container, and the resource are joined with a separator
    that cannot occur in any of them, so no two different identities can be
    concatenated into the same string. The credential is deliberately not part
    of the input: two links to the same resource, one with a key and one
    without, address the same entry.
    """
    identity = "\x00".join((ref.provider, ref.parent_id or "", ref.resource_id))
    return sha256(identity.encode("utf-8")).hexdigest()[:KEY_DIGEST_LENGTH]
