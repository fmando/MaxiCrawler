"""SQLite cache over the library's own directories.

The library is a directory tree that describes itself, and that is the authority
(ADR-010). This table is a *cache* of what those directories say, and nothing
here is ever allowed to become the thing that answers a question the file system
could have answered better. Two properties keep that honest, and both are
enforced above rather than here:

* only *set* questions are answered from this table — a listing, and later
  "is this URL among them?". A single entry is still read from its own
  directory, so a stale row can delay a listing and can never serve the wrong
  file;
* a row is only trusted while the document it was read from has the same
  modification time and size. What that buys is the point of the whole table:
  a listing that used to parse one JSON document per entry now parses only the
  ones that changed.

The verbatim document is stored beside the extracted columns rather than instead
of them. Extracting every member would mean this adapter knowing what a metadata
document contains, which is the library's business and changes when it does; a
document kept whole survives a release that adds a member, because the layer
that understands it is the layer that reads it back.

``entry_id`` is reserved and not written yet. A stable identity for a stored
resource is a decision about the *download* path — the record is rebuilt on
every status change today — and it is deliberately not made here. The column
exists so that making it later is a write rather than a migration, which
``CREATE TABLE IF NOT EXISTS`` would not give us: it does nothing at all to a
table that already exists. **A column added after a release therefore has to be
declared in** :data:`ADDED_COLUMNS` **as well as in the schema below** — which is
what happened when judgements arrived, and what the two entries there are.
"""

import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass

from maxicrawler.database.sqlite import SQLiteDatabase

TABLE = "library_entries"
"""The one table this adapter owns."""

ADDED_COLUMNS: Mapping[str, str] = {
    "verdict": "TEXT NOT NULL DEFAULT ''",
    "favourite": "INTEGER NOT NULL DEFAULT 0",
}
"""Columns that arrived after ``library_entries`` was first released.

Exactly what the module docstring below predicted would be needed, handled the
way :data:`maxicrawler.database.crawls.ADDED_COLUMNS` handles it: ``CREATE TABLE
IF NOT EXISTS`` does nothing to a table that already exists, so a database from
the previous release keeps its old shape until these are appended.

Each definition carries a default, because an existing row has to stay valid
without being rewritten. A row written before this release therefore reads as
unreviewed and unstarred, which is the truth about it.
"""

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS library_entries (
        root TEXT NOT NULL,
        directory TEXT NOT NULL,
        key TEXT NOT NULL,
        mtime_ns INTEGER NOT NULL,
        size INTEGER NOT NULL,
        source_url TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT '',
        verdict TEXT NOT NULL DEFAULT '',
        favourite INTEGER NOT NULL DEFAULT 0,
        checksum TEXT,
        entry_id TEXT,
        document TEXT NOT NULL,
        PRIMARY KEY (root, directory, key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_library_entries_url ON library_entries(root, source_url)",
)
"""The cache table, keyed by which library a row came from.

One database may serve several libraries — a person pointing ``--library`` at an
archive disk has two — so the root is part of the key rather than an assumption.
"""


@dataclass(frozen=True, slots=True)
class IndexedEntry:
    """One library entry as the cache holds it.

    ``directory`` and ``key`` are the two path components that address the entry
    inside its library, which is what makes a row resolvable back to a directory
    without storing an absolute path per entry.
    """

    directory: str
    key: str
    mtime_ns: int
    """Modification time of the metadata document when it was read."""

    size: int
    """Size of the metadata document when it was read."""

    document: str
    """The metadata document, exactly as it was on disk."""

    source_url: str = ""
    """Where the resource came from, without its fragment.

    Extracted so that *"is this URL in the library?"* is an indexed lookup rather
    than a parse of every cached document. Empty for a document that could not be
    parsed, which is also how such a row is recognised.
    """

    status: str = ""
    verdict: str = ""
    """What somebody decided about it, empty for a document that would not parse.

    Written from the release that introduced judgements although nothing reads
    it yet, for the reason :attr:`checksum` gives: counting how many entries are
    unreviewed is a query over this column, and a column filled from the start
    needs no reindex to become one.
    """

    favourite: bool = False
    checksum: str | None = None
    """The SHA-256 of the payload, when the record states one.

    Written from the first release that has this table, although nothing reads it
    yet: content-based duplicate detection is a query over this column, and a
    column that was filled from the start does not need a reindex to become
    useful.
    """

    entry_id: str | None = None
    """Reserved; see the module docstring."""

    @property
    def identity(self) -> tuple[str, str]:
        """Return what addresses this entry within its library."""
        return (self.directory, self.key)

    def describes(self, mtime_ns: int, size: int) -> bool:
        """Return whether this row still describes a document with that stamp."""
        return self.mtime_ns == mtime_ns and self.size == size


class SQLiteLibraryIndex:
    """Caches what the library's metadata documents say, per library root.

    Composes :class:`SQLiteDatabase` and opens a short-lived connection per
    operation, matching the adapters beside it.
    """

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    @property
    def database(self) -> SQLiteDatabase:
        """Return the underlying database adapter."""
        return self._database

    def initialize(self) -> tuple[str, ...]:
        """Create the cache table if it does not exist, and return what was added.

        A table from an earlier release is brought up to the shape above rather
        than left behind; see :data:`ADDED_COLUMNS`.
        """
        with closing(self._database.connect()) as connection, connection:
            for statement in SCHEMA:
                connection.execute(statement)
        return self._database.add_missing_columns(TABLE, ADDED_COLUMNS)

    def entries(self, root: str) -> dict[tuple[str, str], IndexedEntry]:
        """Return everything cached for the library at *root*, by identity.

        Ordered by the two path components, which is the order
        :meth:`~maxicrawler.library.store.Library.entries` walks the directories
        in. A caller that replaces a filesystem walk with this one then gets the
        same sequence rather than one that merely holds the same rows.
        """
        with closing(self._database.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM library_entries WHERE root = ? ORDER BY directory, key",
                (root,),
            ).fetchall()
        cached = (_to_entry(row) for row in rows)
        return {entry.identity: entry for entry in cached}

    def refresh(
        self,
        root: str,
        *,
        updated: Iterable[IndexedEntry] = (),
        removed: Iterable[tuple[str, str]] = (),
    ) -> None:
        """Write *updated* and drop *removed*, in one transaction.

        Both halves together, because they are one observation of the library.
        Committing the additions and then the removals would leave a moment in
        which a listing could count an entry twice — once where it was and once
        where it has been moved to.
        """
        with closing(self._database.connect()) as connection, connection:
            connection.executemany(
                "INSERT INTO library_entries("
                "root, directory, key, mtime_ns, size, source_url, status, "
                "verdict, favourite, checksum, entry_id, document"
                ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(root, directory, key) DO UPDATE SET "
                "mtime_ns = excluded.mtime_ns, size = excluded.size, "
                "source_url = excluded.source_url, status = excluded.status, "
                "verdict = excluded.verdict, favourite = excluded.favourite, "
                "checksum = excluded.checksum, entry_id = excluded.entry_id, "
                "document = excluded.document",
                tuple(
                    (
                        root,
                        entry.directory,
                        entry.key,
                        entry.mtime_ns,
                        entry.size,
                        entry.source_url,
                        entry.status,
                        entry.verdict,
                        int(entry.favourite),
                        entry.checksum,
                        entry.entry_id,
                        entry.document,
                    )
                    for entry in updated
                ),
            )
            connection.executemany(
                "DELETE FROM library_entries WHERE root = ? AND directory = ? AND key = ?",
                tuple((root, directory, key) for directory, key in removed),
            )

    def forget(self, root: str) -> None:
        """Drop everything cached for the library at *root*.

        Nothing calls this in the course of a listing. It exists because a cache
        that cannot be thrown away is not a cache, and because a test that proves
        the index is rebuildable needs to be able to start from nothing.
        """
        with closing(self._database.connect()) as connection, connection:
            connection.execute("DELETE FROM library_entries WHERE root = ?", (root,))

    def __repr__(self) -> str:
        """Return a representation naming the database, not its contents."""
        return f"{type(self).__name__}(database={self._database.path!s})"


def _to_entry(row: sqlite3.Row) -> IndexedEntry:
    """Convert a database row into an :class:`IndexedEntry`."""
    checksum = row["checksum"]
    entry_id = row["entry_id"]
    return IndexedEntry(
        directory=str(row["directory"]),
        key=str(row["key"]),
        mtime_ns=int(row["mtime_ns"]),
        size=int(row["size"]),
        document=str(row["document"]),
        source_url=str(row["source_url"]),
        status=str(row["status"]),
        verdict=str(row["verdict"]),
        favourite=bool(row["favourite"]),
        checksum=None if checksum is None else str(checksum),
        entry_id=None if entry_id is None else str(entry_id),
    )
