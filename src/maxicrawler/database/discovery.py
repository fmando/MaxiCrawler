"""SQLite adapter persisting discovery sessions and their results.

The adapter satisfies :class:`~maxicrawler.crawler.DiscoveryRepository`
structurally and deliberately does not import it, so the ``database`` package
stays independent of the discovery layer.
"""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime

from maxicrawler.database.sqlite import SQLiteDatabase
from maxicrawler.domain import DiscoveryResult, ScanSession, Statistics, UrlRecord

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS scan_sessions (
        session_id TEXT PRIMARY KEY,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        documents_processed INTEGER NOT NULL DEFAULT 0,
        discovered_urls INTEGER NOT NULL DEFAULT 0,
        duplicate_urls INTEGER NOT NULL DEFAULT 0,
        unresolved_urls INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS discovered_urls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL REFERENCES scan_sessions(session_id),
        raw_url TEXT NOT NULL,
        normalized_url TEXT NOT NULL,
        source_url TEXT,
        plugin_name TEXT,
        category TEXT,
        UNIQUE(session_id, normalized_url)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_discovered_urls_session ON discovered_urls(session_id)",
)


def _resolution_columns(result: DiscoveryResult) -> tuple[str | None, str | None]:
    """Return the plugin name and category columns for *result*."""
    resolution = result.resolution
    if resolution is None:
        return None, None
    plugin_name = None if resolution.plugin is None else resolution.plugin.name
    category = (
        None if resolution.classification is None else str(resolution.classification.category)
    )
    return plugin_name, category


@dataclass(frozen=True, slots=True)
class StoredUrl:
    """A discovery result as it was persisted."""

    record: UrlRecord
    plugin_name: str | None
    category: str | None


@dataclass(frozen=True, slots=True)
class StoredSession:
    """A discovery session as it was persisted."""

    session_id: str
    started_at: datetime
    finished_at: datetime | None
    statistics: Statistics


class SQLiteDiscoveryRepository:
    """Stores discovery sessions and their URLs in SQLite.

    The repository composes :class:`SQLiteDatabase` rather than extending it,
    and opens a short-lived connection per operation, matching the existing
    adapter's stateless design.
    """

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    @property
    def database(self) -> SQLiteDatabase:
        """Return the underlying database adapter."""
        return self._database

    def initialize(self) -> None:
        """Create the discovery tables if they do not exist yet."""
        with closing(self._database.connect()) as connection, connection:
            for statement in SCHEMA:
                connection.execute(statement)

    def start_session(self, session: ScanSession) -> None:
        """Record the beginning of *session*, replacing any earlier run of it."""
        with closing(self._database.connect()) as connection, connection:
            connection.execute(
                "INSERT INTO scan_sessions(session_id, started_at) VALUES(?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "started_at = excluded.started_at, finished_at = NULL",
                (session.session_id, session.started_at.isoformat()),
            )

    def save_result(self, session: ScanSession, result: DiscoveryResult) -> None:
        """Persist one discovery result belonging to *session*.

        Storing the same normalized URL twice within a session is a no-op, so
        callers may re-save results without creating duplicate rows.
        """
        plugin_name, category = _resolution_columns(result)
        with closing(self._database.connect()) as connection, connection:
            connection.execute(
                "INSERT INTO discovered_urls("
                "session_id, raw_url, normalized_url, source_url, plugin_name, category"
                ") VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id, normalized_url) DO NOTHING",
                (
                    session.session_id,
                    result.record.raw_url,
                    result.record.normalized_url,
                    result.record.source_url,
                    plugin_name,
                    category,
                ),
            )

    def finish_session(self, session: ScanSession, statistics: Statistics) -> None:
        """Record the completion of *session* together with its counters."""
        with closing(self._database.connect()) as connection, connection:
            connection.execute(
                "UPDATE scan_sessions SET finished_at = ?, documents_processed = ?, "
                "discovered_urls = ?, duplicate_urls = ?, unresolved_urls = ? "
                "WHERE session_id = ?",
                (
                    datetime.now(session.started_at.tzinfo).isoformat(),
                    statistics.documents_processed,
                    statistics.discovered_urls,
                    statistics.duplicate_urls,
                    statistics.unresolved_urls,
                    session.session_id,
                ),
            )

    def stored_session(self, session_id: str) -> StoredSession | None:
        """Return the persisted session called *session_id*, if any."""
        with closing(self._database.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM scan_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        finished_at = row["finished_at"]
        return StoredSession(
            session_id=str(row["session_id"]),
            started_at=datetime.fromisoformat(str(row["started_at"])),
            finished_at=None if finished_at is None else datetime.fromisoformat(str(finished_at)),
            statistics=Statistics(
                documents_processed=int(row["documents_processed"]),
                discovered_urls=int(row["discovered_urls"]),
                duplicate_urls=int(row["duplicate_urls"]),
                unresolved_urls=int(row["unresolved_urls"]),
            ),
        )

    def stored_urls(self, session_id: str) -> tuple[StoredUrl, ...]:
        """Return the URLs persisted for *session_id*, in insertion order."""
        with closing(self._database.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM discovered_urls WHERE session_id = ? ORDER BY id", (session_id,)
            ).fetchall()
        return tuple(self._to_stored_url(row) for row in rows)

    @staticmethod
    def _to_stored_url(row: sqlite3.Row) -> StoredUrl:
        """Convert a database row into a :class:`StoredUrl`."""
        source_url = row["source_url"]
        plugin_name = row["plugin_name"]
        category = row["category"]
        return StoredUrl(
            record=UrlRecord(
                raw_url=str(row["raw_url"]),
                normalized_url=str(row["normalized_url"]),
                source_url=None if source_url is None else str(source_url),
            ),
            plugin_name=None if plugin_name is None else str(plugin_name),
            category=None if category is None else str(category),
        )
