"""Tests for the cache that sits in front of the library's directories.

The library is the authority and the index is a cache (ADR-010), so most of
what is worth asserting here is not "the listing is right" — ``test_app_library``
already asks that — but *"the listing is still right when the cache is wrong"*.
Each test below picks one way for the two to disagree and states which of them
wins.

Documents are written by hand. No provider, no socket, no download.
"""

import json
import os
import shutil
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from maxicrawler.app import LibraryService
from maxicrawler.app.library import _read_document
from maxicrawler.config import Settings
from maxicrawler.database import IndexedEntry, SQLiteDatabase, SQLiteLibraryIndex
from maxicrawler.database.library import ADDED_COLUMNS, SCHEMA
from maxicrawler.domain import DownloadStatus, ResourceKind, ResourceRef
from maxicrawler.library import Library

PAYLOAD = b"payload"
CHECKSUM = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"


def make_service(tmp_path: Path) -> tuple[LibraryService, Library, SQLiteLibraryIndex]:
    """Return a service, its library, and the index it reads through."""
    library = Library(tmp_path / "library")
    database = SQLiteDatabase(tmp_path / "maxicrawler.db")
    index = SQLiteLibraryIndex(database)
    index.initialize()
    settings = Settings(library_path=library.root, database_path=database.path)
    return LibraryService(settings, library=library, index=index), library, index


def write(
    library: Library,
    handle: str,
    *,
    provider: str = "mega",
    name: str | None = "Jump.pdf",
    status: DownloadStatus = DownloadStatus.COMPLETED,
    checksum: str | None = CHECKSUM,
) -> str:
    """Write one library entry by hand and return its key."""
    ref = ResourceRef(
        provider=provider,
        resource_id=handle,
        kind=ResourceKind.FILE,
        url=f"https://{provider}.nz/file/{handle}",
    )
    entry = library.entry(ref)
    entry.path.mkdir(parents=True, exist_ok=True)
    stored = entry.content_path("Jump.pdf")
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(PAYLOAD)
    document = {
        "schema": 1,
        "provider": provider,
        "key": entry.key,
        "resource_id": handle,
        "parent_id": None,
        "kind": "file",
        "name": name,
        "source_url": ref.url,
        "source_document": None,
        "status": status.value,
        "discovered_at": None,
        "downloaded_at": datetime(2026, 8, 9, 14, 30, tzinfo=UTC).isoformat(),
        "attempts": 1,
        "error": None,
        "content": {
            "filename": "Jump.pdf",
            "path": "content/Jump.pdf",
            "size": len(PAYLOAD),
            "checksums": [] if checksum is None else [{"algorithm": "sha256", "value": checksum}],
        },
    }
    entry.metadata_path.write_text(json.dumps(document), encoding="utf-8")
    return entry.key


def rewrite(library: Library, provider: str, key: str, **changes: object) -> None:
    """Change members of an entry's document in place, keeping it valid JSON."""
    path = library.root / provider / key / "metadata.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document.update(changes)
    path.write_text(json.dumps(document), encoding="utf-8")


class CountingReads:
    """Counts how many metadata documents were opened, and reads them normally."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self, path: Path) -> str | None:
        self.count += 1
        return _read_document(path)


class BrokenIndex:
    """An index that refuses every question the way a locked database does."""

    def entries(self, root: str) -> dict[tuple[str, str], IndexedEntry]:
        """Refuse to answer."""
        msg = "database is locked"
        raise sqlite3.OperationalError(msg)

    def refresh(
        self,
        root: str,
        *,
        updated: Iterable[IndexedEntry] = (),
        removed: Iterable[tuple[str, str]] = (),
    ) -> None:
        """Refuse to answer."""
        msg = "database is locked"
        raise sqlite3.OperationalError(msg)


# --- the cache holds what the directories say ---------------------------------


def test_a_listing_fills_the_index(tmp_path: Path) -> None:
    service, library, index = make_service(tmp_path)
    write(library, "one")
    write(library, "two")

    page = service.browse()

    assert page.total == 2
    cached = index.entries(str(library.root.resolve()))
    assert len(cached) == 2
    assert {entry.source_url for entry in cached.values()} == {
        "https://mega.nz/file/one",
        "https://mega.nz/file/two",
    }


def test_the_checksum_is_cached_although_nothing_reads_it_yet(tmp_path: Path) -> None:
    """Written from the first release, so duplicate detection needs no reindex."""
    service, library, index = make_service(tmp_path)
    write(library, "one")

    service.browse()

    (cached,) = index.entries(str(library.root.resolve())).values()
    assert cached.checksum == CHECKSUM
    assert cached.entry_id is None


def test_a_second_listing_parses_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The saving the index exists for, stated as the number it changes.

    The directories are still walked and every document is still ``stat``-ed —
    that is what makes a cached row safe to believe. What does not happen again
    is the reading.
    """
    service, library, _ = make_service(tmp_path)
    for handle in ("one", "two", "three"):
        write(library, handle)
    reads = CountingReads()
    monkeypatch.setattr("maxicrawler.app.library._read_document", reads)

    first = service.browse()
    after_first = reads.count
    second = service.browse()

    assert after_first == 3
    assert reads.count == 3
    assert [item.name for item in second.items] == [item.name for item in first.items]


def test_a_changed_document_is_read_again(tmp_path: Path) -> None:
    service, library, _ = make_service(tmp_path)
    key = write(library, "one")
    service.browse()

    rewrite(library, "mega", key, name="Renamed.pdf")
    (item,) = service.browse().items

    assert item.name == "Renamed.pdf"


def test_a_document_rewritten_to_the_same_length_is_still_read_again(tmp_path: Path) -> None:
    """Two values, not one: a modification time alone has a resolution."""
    service, library, _ = make_service(tmp_path)
    key = write(library, "one")
    service.browse()
    path = library.root / "mega" / key / "metadata.json"
    stamp = path.stat()

    rewrite(library, "mega", key, name="Jumped.pdf")
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    (item,) = service.browse().items

    assert item.name == "Jumped.pdf"


def test_a_removed_entry_leaves_the_listing_and_the_index(tmp_path: Path) -> None:
    service, library, index = make_service(tmp_path)
    key = write(library, "one")
    write(library, "two")
    service.browse()

    shutil.rmtree(library.root / "mega" / key)
    page = service.browse()

    assert page.total == 1
    assert len(index.entries(str(library.root.resolve()))) == 1


def test_an_entry_added_by_hand_is_found(tmp_path: Path) -> None:
    """A library is something you may ``rsync`` into place (ADR-010).

    The walk is what keeps that true, which is why the index caches the reading
    of documents and never the list of entries.
    """
    service, library, _ = make_service(tmp_path)
    write(library, "one")
    service.browse()

    write(library, "two")

    assert service.browse().total == 2


# --- and never becomes the authority ------------------------------------------


def test_one_entry_is_read_from_the_file_system_not_the_cache(tmp_path: Path) -> None:
    """The rule that makes a stale row harmless: sets go through the index, one
    entry does not."""
    service, library, index = make_service(tmp_path)
    key = write(library, "one")
    service.browse()
    index.refresh(
        str(library.root.resolve()),
        updated=(
            IndexedEntry(
                directory="mega",
                key=key,
                mtime_ns=1,
                size=1,
                document=json.dumps({"schema": 1, "name": "A lie"}),
                source_url="https://mega.nz/file/nowhere",
            ),
        ),
    )

    item = service.item("mega", key)

    assert item is not None
    assert item.name == "Jump.pdf"
    assert item.source_url == "https://mega.nz/file/one"


def test_a_listing_survives_a_database_that_refuses(tmp_path: Path) -> None:
    library = Library(tmp_path / "library")
    settings = Settings(library_path=library.root, database_path=tmp_path / "maxicrawler.db")
    service = LibraryService(settings, library=library, index=BrokenIndex())  # type: ignore[arg-type]
    write(library, "one")

    page = service.browse()

    assert page.total == 1
    assert page.items[0].name == "Jump.pdf"


def test_a_database_that_cannot_be_opened_is_not_an_error(tmp_path: Path) -> None:
    """A directory where the database file should be: unopenable, and harmless."""
    library = Library(tmp_path / "library")
    occupied = tmp_path / "maxicrawler.db"
    occupied.mkdir()
    service = LibraryService(Settings(library_path=library.root, database_path=occupied))
    write(library, "one")

    page = service.browse()

    assert page.total == 1


def test_the_index_is_rebuildable(tmp_path: Path) -> None:
    """A cache you cannot throw away is not a cache."""
    service, library, index = make_service(tmp_path)
    write(library, "one")
    write(library, "two")
    before = service.browse()

    index.forget(str(library.root.resolve()))
    after = service.browse()

    assert [item.key for item in after.items] == [item.key for item in before.items]


def test_a_damaged_document_is_skipped_and_not_read_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is cached as unreadable, so one broken entry costs one read, not one per page."""
    service, library, _ = make_service(tmp_path)
    write(library, "one")
    key = write(library, "two")
    (library.root / "mega" / key / "metadata.json").write_text("{not json", encoding="utf-8")
    reads = CountingReads()
    monkeypatch.setattr("maxicrawler.app.library._read_document", reads)

    first = service.browse()
    after_first = reads.count
    second = service.browse()

    assert first.total == 1
    assert second.total == 1
    assert after_first == 2
    assert reads.count == 2


def test_two_libraries_share_one_database_without_mixing(tmp_path: Path) -> None:
    """The root is part of the key, because ``--library`` can point anywhere."""
    database = SQLiteDatabase(tmp_path / "maxicrawler.db")
    index = SQLiteLibraryIndex(database)
    index.initialize()
    first = Library(tmp_path / "one")
    second = Library(tmp_path / "two")
    write(first, "a")
    write(second, "b")
    write(second, "c")

    one = LibraryService(
        Settings(library_path=first.root, database_path=database.path), library=first, index=index
    ).browse()
    two = LibraryService(
        Settings(library_path=second.root, database_path=database.path), library=second, index=index
    ).browse()

    assert one.total == 1
    assert two.total == 2


def test_one_library_named_two_ways_is_one_library(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "maxicrawler.db")
    index = SQLiteLibraryIndex(database)
    index.initialize()
    library = Library(tmp_path / "library")
    write(library, "one")
    spelled = Library(tmp_path / "." / "library")

    LibraryService(
        Settings(library_path=library.root, database_path=database.path),
        library=library,
        index=index,
    ).browse()
    LibraryService(
        Settings(library_path=spelled.root, database_path=database.path),
        library=spelled,
        index=index,
    ).browse()

    with sqlite3.connect(database.path) as connection:
        (rows,) = connection.execute("SELECT COUNT(*) FROM library_entries").fetchone()
    assert rows == 1


# --- the answer a report marks its rows with ----------------------------------


def test_a_url_the_library_holds_something_from_is_recognised(tmp_path: Path) -> None:
    service, library, _ = make_service(tmp_path)
    write(library, "one")

    assert service.stored(["https://mega.nz/file/one"]) == frozenset({"https://mega.nz/file/one"})


def test_a_url_the_library_never_saw_is_not(tmp_path: Path) -> None:
    service, library, _ = make_service(tmp_path)
    write(library, "one")

    assert service.stored(["https://mega.nz/file/two"]) == frozenset()


def test_the_key_survives_the_question(tmp_path: Path) -> None:
    """A share link is its key, and a caller handed back a key-less URL has lost it.

    The fragment is stripped to compare — a record never holds one (ADR-020) —
    and kept in the answer, because the answer is a set of the URLs as asked.
    """
    service, library, _ = make_service(tmp_path)
    write(library, "one")
    asked = "https://mega.nz/file/one#Aa0123456789bCdEfGhIjKlMnOpQrStUvWxYz"

    assert service.stored([asked]) == frozenset({asked})


def test_a_container_is_recognised_from_one_stored_file(tmp_path: Path) -> None:
    """Why this is a state and not a sentence about being downloaded.

    A share naming a folder is recorded by each file inside it, under the
    container's own URL — see the Mega provider, which gives every child the
    parent's link. One stored file therefore answers for the folder, and what
    reaches a person has to be "the library knows this", never "you have it all".
    """
    service, library, _ = make_service(tmp_path)
    folder = "https://mega.nz/folder/AaBbCcDd"
    for handle in ("child-one", "child-two"):
        ref = ResourceRef(
            provider="mega",
            resource_id=handle,
            kind=ResourceKind.FILE,
            url=folder,
            parent_id="AaBbCcDd",
        )
        entry = library.entry(ref)
        entry.path.mkdir(parents=True, exist_ok=True)
        entry.metadata_path.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "provider": "mega",
                    "key": entry.key,
                    "resource_id": handle,
                    "parent_id": "AaBbCcDd",
                    "kind": "file",
                    "name": f"{handle}.pdf",
                    "source_url": folder,
                    "source_document": None,
                    "status": "completed",
                    "discovered_at": None,
                    "downloaded_at": None,
                    "attempts": 1,
                    "error": None,
                    "content": None,
                }
            ),
            encoding="utf-8",
        )

    assert service.stored([folder]) == frozenset({folder})


def test_asking_about_nothing_reads_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, library, _ = make_service(tmp_path)
    write(library, "one")
    reads = CountingReads()
    monkeypatch.setattr("maxicrawler.app.library._read_document", reads)

    assert service.stored([]) == frozenset()
    assert reads.count == 0


def test_the_answer_parses_no_metadata_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The URL is a column on the index, so this question never reads a record."""
    service, library, _ = make_service(tmp_path)
    write(library, "one")
    service.stored(["https://mega.nz/file/one"])
    reads = CountingReads()
    monkeypatch.setattr("maxicrawler.app.library._read_document", reads)

    again = service.stored(["https://mega.nz/file/one"])

    assert again == frozenset({"https://mega.nz/file/one"})
    assert reads.count == 0


def test_the_answer_is_still_right_without_an_index(tmp_path: Path) -> None:
    library = Library(tmp_path / "library")
    settings = Settings(library_path=library.root, database_path=tmp_path / "maxicrawler.db")
    service = LibraryService(settings, library=library, index=BrokenIndex())  # type: ignore[arg-type]
    write(library, "one")

    assert service.stored(["https://mega.nz/file/one"]) == frozenset({"https://mega.nz/file/one"})


def test_an_entry_stored_since_the_last_question_is_seen(tmp_path: Path) -> None:
    service, library, _ = make_service(tmp_path)
    write(library, "one")
    service.stored(["https://mega.nz/file/one"])

    write(library, "two")

    assert service.stored(["https://mega.nz/file/two"]) == frozenset({"https://mega.nz/file/two"})


# --- the columns that arrived with judgements ---------------------------------


def test_a_table_from_the_previous_release_gains_the_new_columns(tmp_path: Path) -> None:
    """`CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists."""
    database = SQLiteDatabase(tmp_path / "library.db")
    with closing(database.connect()) as connection, connection:
        connection.execute(
            "CREATE TABLE library_entries ("
            "root TEXT NOT NULL, directory TEXT NOT NULL, key TEXT NOT NULL, "
            "mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL, "
            "source_url TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '', "
            "checksum TEXT, entry_id TEXT, document TEXT NOT NULL, "
            "PRIMARY KEY (root, directory, key))"
        )
        connection.execute(
            "INSERT INTO library_entries("
            "root, directory, key, mtime_ns, size, source_url, status, document"
            ") VALUES('/lib', 'mega', 'abc', 1, 2, 'https://mega.nz/file/x', 'completed', '{}')"
        )

    added = SQLiteLibraryIndex(database).initialize()

    assert set(added) == set(ADDED_COLUMNS)
    rows = SQLiteLibraryIndex(database).entries("/lib")
    assert rows[("mega", "abc")].verdict == ""
    assert rows[("mega", "abc")].favourite is False


def test_a_judgement_is_cached_beside_the_document(tmp_path: Path) -> None:
    index = SQLiteLibraryIndex(SQLiteDatabase(tmp_path / "library.db"))
    index.initialize()

    index.refresh(
        "/lib",
        updated=[
            IndexedEntry(
                directory="mega",
                key="abc",
                mtime_ns=1,
                size=2,
                document="{}",
                verdict="kept",
                favourite=True,
            )
        ],
    )

    row = index.entries("/lib")[("mega", "abc")]
    assert row.verdict == "kept"
    assert row.favourite is True


def test_the_declared_columns_match_the_schema() -> None:
    """Forgetting an entry here fails a test rather than an operator's library."""
    created = SCHEMA[0]
    for column in ADDED_COLUMNS:
        assert f"{column} " in created
