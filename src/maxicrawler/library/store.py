"""The on-disk library: one directory per downloaded resource.

The layout is deliberately boring, because it has to outlive every other
decision in this project:

```text
<root>/
    library.json                 the store descriptor and its schema version
    <provider>/                  one namespace per provider
        <resource-key>/          one directory per resource
            metadata.json        what this resource is and where it came from
            content/             the payload, exactly as the provider named it
            .incomplete/         in-flight files; never a finished download
```

Four properties follow from it, and each of them is the reason a simpler
layout was rejected:

* **The file system is the source of truth.** Every entry describes itself, so
  a library survives losing a database, can be moved with ``rsync``, and can be
  read by a human with nothing but a text editor. An index may be added later
  as a cache, never as the authority.
* **The payload and the metadata cannot collide.** A provider is free to name
  a file ``metadata.json``; putting the payload in its own directory makes that
  harmless instead of destructive.
* **A partial download is never mistaken for a finished one.** Content is
  written under ``.incomplete`` and moved into place only once it is whole, so
  an interrupted run leaves no half file that a later run would skip over.
* **An entry is addressed by identity, not by name.** The directory of a
  resource is derived from its reference, so renaming a remote file, or reading
  it through a link that carries no key, still finds the same entry.

Nothing in this module knows what a provider is beyond its name.
"""

import json
import os
from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from maxicrawler import __version__
from maxicrawler.domain import ResourceRef
from maxicrawler.library.errors import LibraryError, LibraryRecordError
from maxicrawler.library.naming import provider_directory, resource_key, safe_filename
from maxicrawler.library.records import (
    CONTENT_DIRECTORY,
    METADATA_FILENAME,
    ResourceRecord,
)

DEFAULT_LIBRARY_PATH = Path("library")
"""Where downloads are stored when no other location is configured."""

DESCRIPTOR_FILENAME = "library.json"
"""Name of the document identifying a directory as a MaxiCrawler library."""

LIBRARY_SCHEMA = 1
"""Version of the library layout this release creates."""

STAGING_DIRECTORY = ".incomplete"
"""Directory holding files that are still being written.

The leading dot keeps it out of the way of the discovery loader, which skips
hidden directories, and marks it as machinery rather than content.
"""


class LibraryEntry:
    """One resource's own directory inside the library.

    An entry is a value-like handle: constructing it touches nothing, so a
    caller can ask whether a resource is already present without creating a
    directory for it. The directory appears when something is written.
    """

    __slots__ = ("_key", "_path", "_provider")

    def __init__(self, path: Path, *, provider: str, key: str) -> None:
        self._path = path
        self._provider = provider
        self._key = key

    @property
    def path(self) -> Path:
        """Return the directory this entry owns."""
        return self._path

    @property
    def provider(self) -> str:
        """Return the name of the provider the resource came from."""
        return self._provider

    @property
    def key(self) -> str:
        """Return the identity-derived key naming this entry."""
        return self._key

    @property
    def metadata_path(self) -> Path:
        """Return the path of the metadata document."""
        return self._path / METADATA_FILENAME

    @property
    def content_directory(self) -> Path:
        """Return the directory the payload is stored in."""
        return self._path / CONTENT_DIRECTORY

    @property
    def staging_directory(self) -> Path:
        """Return the directory an in-flight payload is written to."""
        return self._path / STAGING_DIRECTORY

    def exists(self) -> bool:
        """Return whether anything has been stored for this resource."""
        return self.metadata_path.exists()

    def read(self) -> ResourceRecord | None:
        """Return the stored record, or ``None`` when there is none.

        Raises:
            LibraryRecordError: a document exists but cannot be read. It is
                never treated as absent, because that would invite overwriting
                the only account of what the entry holds.
        """
        try:
            raw = self.metadata_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as error:
            msg = f"metadata could not be read: {self.metadata_path}"
            raise LibraryRecordError(msg) from error
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as error:
            msg = f"metadata is not valid JSON: {self.metadata_path}"
            raise LibraryRecordError(msg) from error
        if not isinstance(document, dict):
            msg = f"metadata is not a JSON object: {self.metadata_path}"
            raise LibraryRecordError(msg)
        return ResourceRecord.from_document(document)

    def write(self, record: ResourceRecord) -> None:
        """Store *record*, replacing any previous one atomically.

        The document is written beside its destination and moved into place, so
        a crash mid-write leaves the previous metadata intact rather than a
        truncated file that nothing can read.
        """
        self._path.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(record.to_document(), indent=2, ensure_ascii=False) + "\n"
        _replace_atomically(self.metadata_path, payload)

    def is_complete(self) -> bool:
        """Return whether a finished payload is stored for this resource.

        Both halves are checked. A record claiming completion whose file has
        been deleted is not complete, which is what makes a library repairable
        by simply running the download again.
        """
        try:
            record = self.read()
        except LibraryRecordError:
            return False
        if record is None or not record.is_complete or record.content is None:
            return False
        return (self._path / record.content.path).is_file()

    def content_path(self, filename: str) -> Path:
        """Return where the payload called *filename* belongs.

        The name is sanitized here rather than trusted, because it comes from
        the provider.
        """
        return self.content_directory / safe_filename(filename)

    def reserve(self, filename: str) -> Path:
        """Return a path under ``.incomplete`` to write *filename* into.

        The staging directory is created; the file is not.
        """
        try:
            self.staging_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            msg = f"library entry could not be prepared: {self.staging_directory}"
            raise LibraryError(msg) from error
        return self.staging_directory / safe_filename(filename)

    def commit(self, staged: Path, filename: str) -> Path:
        """Move the finished *staged* file into the content directory.

        Returns the final path. The move replaces any earlier payload of the
        same name, which only happens on a deliberate re-download: an entry
        that is already complete is never transferred again.

        Raises:
            LibraryError: the payload could not be put into place.
        """
        destination = self.content_path(filename)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, destination)
        except OSError as error:
            msg = f"payload could not be stored: {destination}"
            raise LibraryError(msg) from error
        return destination

    def discard(self) -> None:
        """Remove anything left under ``.incomplete``.

        Failures are suppressed on purpose: this runs while a download is
        already failing, and losing a temporary file matters less than losing
        the reason the download failed in the first place. A file that survives
        is simply overwritten by the next attempt.
        """
        if not self.staging_directory.is_dir():
            return
        for path in self.staging_directory.iterdir():
            with suppress(OSError):
                path.unlink()
        with suppress(OSError):
            self.staging_directory.rmdir()

    def __repr__(self) -> str:
        """Return a representation naming the entry, not its contents."""
        return f"{type(self).__name__}(provider={self._provider!r}, key={self._key!r})"


class Library:
    """The long-lived store of everything MaxiCrawler has downloaded.

    The library knows about directories, JSON, and identity. It knows nothing
    about providers, transfers, or queues, so it can be pointed at by a CLI
    command, a future GUI, and a future API alike without any of them teaching
    it a new vocabulary.
    """

    def __init__(self, root: Path = DEFAULT_LIBRARY_PATH) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        """Return the directory this library occupies."""
        return self._root

    @property
    def descriptor_path(self) -> Path:
        """Return the path of the store descriptor."""
        return self._root / DESCRIPTOR_FILENAME

    def initialize(self) -> None:
        """Create the library root and its descriptor if they are absent.

        Calling this on an existing library is harmless and leaves the
        descriptor untouched, so the creation date it records stays true.
        """
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            msg = f"library directory could not be created: {self._root}"
            raise LibraryError(msg) from error
        if self.descriptor_path.exists():
            return
        descriptor = {
            "schema": LIBRARY_SCHEMA,
            "generator": f"MaxiCrawler {__version__}",
            "created_at": datetime.now(UTC).isoformat(),
        }
        _replace_atomically(
            self.descriptor_path, json.dumps(descriptor, indent=2, ensure_ascii=False) + "\n"
        )

    def entry(self, ref: ResourceRef) -> LibraryEntry:
        """Return the entry addressing *ref*, whether or not it exists yet."""
        provider = provider_directory(ref.provider)
        key = resource_key(ref)
        return LibraryEntry(self._root / provider / key, provider=ref.provider, key=key)

    def providers(self) -> tuple[str, ...]:
        """Return the provider directories the library holds, sorted."""
        if not self._root.is_dir():
            return ()
        return tuple(
            sorted(
                path.name
                for path in self._root.iterdir()
                if path.is_dir() and not path.name.startswith(".")
            )
        )

    def entries(self, provider: str | None = None) -> Iterator[LibraryEntry]:
        """Yield every stored entry, optionally restricted to one provider.

        Traversal is sorted and reads only directory names, so listing a large
        library does not parse a single metadata document.
        """
        names = (provider_directory(provider),) if provider is not None else self.providers()
        for name in names:
            directory = self._root / name
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                if path.is_dir():
                    yield LibraryEntry(path, provider=name, key=path.name)

    def __repr__(self) -> str:
        """Return a representation naming the root directory."""
        return f"{type(self).__name__}(root={self._root!s})"


def _replace_atomically(destination: Path, payload: str) -> None:
    """Write *payload* to *destination*, replacing it in one step.

    Raises:
        LibraryError: the document could not be written.
    """
    temporary = destination.with_name(f"{destination.name}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, destination)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        msg = f"document could not be written: {destination}"
        raise LibraryError(msg) from error
