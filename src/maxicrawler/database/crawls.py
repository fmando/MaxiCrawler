"""SQLite adapter persisting the summary of a crawl.

The adapter satisfies :class:`~maxicrawler.web.repository.CrawlRepository`
structurally and deliberately does not import it, the same arrangement
:class:`~maxicrawler.database.SQLiteDiscoveryRepository` has with discovery.
It does import the values it has to store, which is the direction this project
already runs in: the implementation depends on the abstraction, the web layer
never imports ``database``, and the composition root binds the two.

A crawl shares its identifier with the discovery session it feeds, so
``crawl_sessions`` and ``scan_sessions`` join on one key and every URL a crawl
found is reachable from the crawl that found it.

Nothing about how requests were made is written here. The report can reach a
:class:`~maxicrawler.web.session.RequestContext` by traversal, and this is
exactly the module where a credential would end up in a file, so no column
exists for one.
"""

import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime

from maxicrawler.database.sqlite import SQLiteDatabase
from maxicrawler.web.report import CrawlReport
from maxicrawler.web.session import CrawlSession, CrawlState

TABLE = "crawl_sessions"
"""The one table this adapter owns."""

ADDED_COLUMNS: Mapping[str, str] = {
    "pages_attempted": "INTEGER NOT NULL DEFAULT 0",
    "respect_robots": "INTEGER NOT NULL DEFAULT 1",
    "below_seed": "INTEGER NOT NULL DEFAULT 0",
}
"""Columns that arrived after ``crawl_sessions`` was first released.

``CREATE TABLE IF NOT EXISTS`` does nothing to a table that already exists, so a
database written by an earlier release keeps the shape it was created with. Every
column added since then is declared here and appended by :meth:`initialize`.

Each definition must carry a default, because an existing row has to stay valid
without being rewritten. ``tests/test_crawl_repository.py`` asserts that this
mapping and the ``CREATE TABLE`` above it stay in step, so forgetting an entry
fails a test rather than an operator's crawl.
"""

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS crawl_sessions (
        session_id TEXT PRIMARY KEY,
        seed_url TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        state TEXT NOT NULL,
        max_depth INTEGER NOT NULL DEFAULT 0,
        max_pages INTEGER NOT NULL DEFAULT 0,
        same_domain INTEGER NOT NULL DEFAULT 0,
        include_subdomains INTEGER NOT NULL DEFAULT 0,
        below_seed INTEGER NOT NULL DEFAULT 0,
        respect_robots INTEGER NOT NULL DEFAULT 1,
        pages_visited INTEGER NOT NULL DEFAULT 0,
        pages_failed INTEGER NOT NULL DEFAULT 0,
        pages_attempted INTEGER NOT NULL DEFAULT 0,
        pages_skipped INTEGER NOT NULL DEFAULT 0,
        links_discovered INTEGER NOT NULL DEFAULT 0,
        max_depth_reached INTEGER NOT NULL DEFAULT 0,
        frontier_remaining INTEGER NOT NULL DEFAULT 0,
        elapsed_seconds REAL NOT NULL DEFAULT 0.0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_crawl_sessions_seed ON crawl_sessions(seed_url)",
)


@dataclass(frozen=True, slots=True)
class StoredCrawl:
    """A crawl summary as it was persisted.

    A summary, not a recipe: the options that defined the *scope* of the crawl
    are kept because they explain the counters beside them, while one that only
    shaped what was read from a page is not.

    The scope fields are held **flat, not as a**
    :class:`~maxicrawler.web.session.CrawlOptions`. A stored row is a record of
    what happened, and reading a record must not re-impose today's rules on it:
    building an options object here made a row whose ``max_pages`` predates the
    current validation crash the reader instead of being reported. What was
    written is what is read back.
    """

    session_id: str
    seed_url: str
    started_at: datetime
    finished_at: datetime | None
    state: CrawlState
    max_depth: int
    max_pages: int
    same_domain: bool
    include_subdomains: bool
    below_seed: bool
    """Whether the run stayed at or below the place its seed URL named.

    Supersedes the two above when set, which is why it is stored beside them
    rather than folded into them: a row says what it was told, and
    :attr:`~maxicrawler.web.session.CrawlOptions.scope` says which of the three
    actually ran. Rows written before this column existed default to *false* —
    the crawler had no way to do it.
    """

    respect_robots: bool
    """Whether this run obeyed the robots.txt of the hosts it visited.

    Kept because it explains the counters beside it, and because a setting
    that has changed since cannot answer it. Rows written before this column
    existed default to *true*, which is what those runs would have done had
    they been asked -- the crawler had no way to disobey.
    """

    pages_visited: int
    pages_failed: int
    pages_attempted: int
    pages_skipped: int
    links_discovered: int
    max_depth_reached: int
    frontier_remaining: int
    elapsed_seconds: float


class SQLiteCrawlRepository:
    """Stores crawl summaries in SQLite.

    Composes :class:`SQLiteDatabase` rather than extending it, and opens a
    short-lived connection per operation, matching the existing adapters.
    """

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    @property
    def database(self) -> SQLiteDatabase:
        """Return the underlying database adapter."""
        return self._database

    def initialize(self) -> tuple[str, ...]:
        """Create the crawl table, or bring an existing one up to date.

        Safe to call on every run, and it has to be: a database written by an
        earlier release is otherwise missing the columns this one writes, and
        the failure lands at the *end* of a crawl — after all the work, when the
        summary is written.

        Returns the columns that had to be appended, so a caller can say what it
        migrated.
        """
        with closing(self._database.connect()) as connection, connection:
            for statement in SCHEMA:
                connection.execute(statement)
        return self._database.add_missing_columns(TABLE, ADDED_COLUMNS)

    def start_crawl(self, session: CrawlSession) -> None:
        """Record the beginning of *session*, replacing any earlier run of it."""
        options = session.options
        with closing(self._database.connect()) as connection, connection:
            connection.execute(
                "INSERT INTO crawl_sessions("
                "session_id, seed_url, started_at, state, max_depth, max_pages, "
                "same_domain, include_subdomains, below_seed, respect_robots"
                ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "seed_url = excluded.seed_url, started_at = excluded.started_at, "
                "finished_at = NULL, state = excluded.state",
                (
                    session.session_id,
                    session.seed_url,
                    session.started_at.isoformat(),
                    str(CrawlState.RUNNING),
                    options.max_depth,
                    options.max_pages,
                    int(options.same_domain),
                    int(options.include_subdomains),
                    int(options.below_seed),
                    int(options.respect_robots),
                ),
            )

    def finish_crawl(self, session: CrawlSession, report: CrawlReport) -> None:
        """Record how *session* ended, together with its counters."""
        statistics = report.statistics
        with closing(self._database.connect()) as connection, connection:
            connection.execute(
                "UPDATE crawl_sessions SET finished_at = ?, state = ?, pages_visited = ?, "
                "pages_failed = ?, pages_attempted = ?, pages_skipped = ?, "
                "links_discovered = ?, max_depth_reached = ?, frontier_remaining = ?, "
                "elapsed_seconds = ? WHERE session_id = ?",
                (
                    report.finished_at.isoformat(),
                    str(report.state),
                    statistics.pages_visited,
                    statistics.pages_failed,
                    statistics.pages_attempted,
                    statistics.pages_skipped,
                    report.links_discovered,
                    statistics.max_depth_reached,
                    statistics.frontier_remaining,
                    statistics.elapsed_seconds,
                    session.session_id,
                ),
            )

    def stored_crawl(self, session_id: str) -> StoredCrawl | None:
        """Return the persisted crawl called *session_id*, if any."""
        with closing(self._database.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM crawl_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return None if row is None else _to_stored_crawl(row)

    def stored_crawls(self) -> tuple[StoredCrawl, ...]:
        """Return every persisted crawl, most recently started first."""
        with closing(self._database.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM crawl_sessions ORDER BY started_at DESC, session_id"
            ).fetchall()
        return tuple(_to_stored_crawl(row) for row in rows)


def _to_stored_crawl(row: sqlite3.Row) -> StoredCrawl:
    """Convert a database row into a :class:`StoredCrawl`."""
    finished_at = row["finished_at"]
    return StoredCrawl(
        session_id=str(row["session_id"]),
        seed_url=str(row["seed_url"]),
        started_at=datetime.fromisoformat(str(row["started_at"])),
        finished_at=None if finished_at is None else datetime.fromisoformat(str(finished_at)),
        state=CrawlState(str(row["state"])),
        max_depth=int(row["max_depth"]),
        max_pages=int(row["max_pages"]),
        same_domain=bool(row["same_domain"]),
        include_subdomains=bool(row["include_subdomains"]),
        below_seed=bool(row["below_seed"]),
        respect_robots=bool(row["respect_robots"]),
        pages_visited=int(row["pages_visited"]),
        pages_failed=int(row["pages_failed"]),
        pages_attempted=int(row["pages_attempted"]),
        pages_skipped=int(row["pages_skipped"]),
        links_discovered=int(row["links_discovered"]),
        max_depth_reached=int(row["max_depth_reached"]),
        frontier_remaining=int(row["frontier_remaining"]),
        elapsed_seconds=float(row["elapsed_seconds"]),
    )
