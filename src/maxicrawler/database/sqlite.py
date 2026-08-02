"""SQLite persistence adapter."""

import sqlite3
from pathlib import Path


class SQLiteDatabase:
    """A small SQLite adapter for application-owned metadata.

    The adapter deliberately provides only generic persistence primitives; no
    crawler-specific schema is introduced in this implementation sprint.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        """Open a connection with rows addressable by column name."""
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

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
