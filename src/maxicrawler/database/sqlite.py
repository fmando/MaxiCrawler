"""SQLite persistence adapter.

Every schema in this package is created with ``CREATE TABLE IF NOT EXISTS``,
which creates a table that does not exist and does **nothing at all** to one
that does. A release that adds a column therefore leaves every existing
database behind, and the failure surfaces at the first write rather than at
startup.

:meth:`SQLiteDatabase.add_missing_columns` closes that for the case that
actually occurs — a column appended to a table — and each adapter declares which
of its columns arrived after the table's first release.

This is not schema versioning, and it is not a substitute for it. It cannot
rename a column, change a type, or backfill a value from another table. When
one of those is needed, the honest answer is a `user_version` and an ordered
list of migrations; the library's ``library.json`` already states a schema
version for exactly this reason (ADR-013). Recorded in ROADMAP.md.
"""

import re
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
"""What a table or column name may look like.

``ALTER TABLE`` cannot take a bound parameter for an identifier, so the name has
to be interpolated. Every caller in this package passes a literal constant, and
this makes that a checked property rather than an assumed one.
"""


def require_identifier(name: str) -> str:
    """Return *name* if it is a plain SQL identifier.

    Raises:
        ValueError: *name* could not be safely interpolated into a statement.
    """
    if not IDENTIFIER.match(name):
        msg = f"not a usable SQL identifier: {name!r}"
        raise ValueError(msg)
    return name


class SQLiteDatabase:
    """A small SQLite adapter for application-owned metadata.

    The adapter deliberately provides only generic persistence primitives; no
    crawler-specific schema is introduced here.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        """Open a connection with rows addressable by column name."""
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def table_columns(self, table: str) -> frozenset[str]:
        """Return the column names of *table*, or nothing when it has none.

        A table that does not exist reports an empty set rather than raising, so
        a caller can ask before it has created anything.
        """
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT name FROM pragma_table_info(?)", (require_identifier(table),)
            ).fetchall()
        return frozenset(str(row["name"]) for row in rows)

    def add_missing_columns(self, table: str, columns: Mapping[str, str]) -> tuple[str, ...]:
        """Append every column of *columns* that *table* does not have yet.

        *columns* maps a name to its SQL definition, which must carry a default
        so an existing row stays valid. Returns the names that were added, in
        declaration order, so a caller can log or test what happened.

        Nothing is done for a table that does not exist: creating it is the
        schema's job, and it will be created with these columns already in it.
        """
        require_identifier(table)
        existing = self.table_columns(table)
        if not existing:
            return ()
        missing = tuple(name for name in columns if name not in existing)
        if not missing:
            return ()
        with closing(self.connect()) as connection, connection:
            for name in missing:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {require_identifier(name)} {columns[name]}"
                )
        return missing

    def initialize(self) -> None:
        """Create the metadata table if it has not been created yet."""
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def set_metadata(self, key: str, value: str) -> None:
        """Store a string value under *key*."""
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_metadata(self, key: str) -> str | None:
        """Return the value stored under *key*, if any."""
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])
