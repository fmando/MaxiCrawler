"""Tests for persisting the summary of a crawl."""

import ast
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from maxicrawler.crawler import DiscoverySummary, PluginUsage
from maxicrawler.database import SQLiteCrawlRepository, SQLiteDatabase
from maxicrawler.database.crawls import ADDED_COLUMNS
from maxicrawler.domain import ScanSession, Statistics
from maxicrawler.web.report import CrawlReport, CrawlStatistics, PageOutcome
from maxicrawler.web.repository import CrawlRepository, NullCrawlRepository
from maxicrawler.web.session import CrawlOptions, CrawlSession, CrawlState, RequestContext

STARTED = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)
FINISHED = datetime(2026, 8, 7, 9, 5, tzinfo=UTC)


@pytest.fixture
def repository(tmp_path: Path) -> SQLiteCrawlRepository:
    """Return an initialized repository over a throwaway database."""
    store = SQLiteCrawlRepository(SQLiteDatabase(tmp_path / "crawls.db"))
    store.initialize()
    return store


def make_session(**kwargs: object) -> CrawlSession:
    """Return a crawl session over example.test."""
    options: dict[str, object] = {
        "session_id": "crawl-1",
        "seed_url": "https://example.test/",
        "started_at": STARTED,
        "options": CrawlOptions(max_depth=2, max_pages=20, same_domain=True),
    }
    options.update(kwargs)
    return CrawlSession(**options)  # type: ignore[arg-type]


def make_report(
    session: CrawlSession,
    *,
    state: CrawlState = CrawlState.COMPLETED,
    pages: tuple[PageOutcome, ...] = (),
) -> CrawlReport:
    """Return a report for *session*."""
    return CrawlReport(
        session=session,
        state=state,
        statistics=CrawlStatistics(
            pages_visited=14,
            pages_failed=1,
            pages_attempted=18,
            pages_skipped=128,
            max_depth_reached=2,
            frontier_remaining=3,
            elapsed_seconds=6.25,
        ),
        summary=DiscoverySummary(
            session=ScanSession("crawl-1", STARTED),
            statistics=Statistics(documents_processed=14, discovered_urls=284, duplicate_urls=128),
            plugin_usage=(PluginUsage("generic", 281),),
        ),
        pages=pages,
        finished_at=FINISHED,
    )


# --- the port ----------------------------------------------------------------


def test_the_null_repository_satisfies_the_protocol() -> None:
    assert isinstance(NullCrawlRepository(), CrawlRepository)


def test_the_sqlite_repository_satisfies_the_protocol(
    repository: SQLiteCrawlRepository,
) -> None:
    assert isinstance(repository, CrawlRepository)


def test_the_null_repository_discards_everything() -> None:
    session = make_session()
    store = NullCrawlRepository()

    store.start_crawl(session)
    store.finish_crawl(session, make_report(session))


# --- the summary round trip --------------------------------------------------


def test_a_started_crawl_is_recorded_before_it_finishes(
    repository: SQLiteCrawlRepository,
) -> None:
    session = make_session()

    repository.start_crawl(session)
    stored = repository.stored_crawl("crawl-1")

    assert stored is not None
    assert stored.seed_url == "https://example.test/"
    assert stored.started_at == STARTED
    assert stored.finished_at is None
    assert stored.state is CrawlState.RUNNING


def test_a_finished_crawl_records_its_state_and_counters(
    repository: SQLiteCrawlRepository,
) -> None:
    session = make_session()
    repository.start_crawl(session)

    repository.finish_crawl(session, make_report(session))
    stored = repository.stored_crawl("crawl-1")

    assert stored is not None
    assert stored.state is CrawlState.COMPLETED
    assert stored.finished_at == FINISHED
    assert stored.pages_visited == 14
    assert stored.pages_failed == 1
    assert stored.pages_skipped == 128
    assert stored.links_discovered == 412
    assert stored.max_depth_reached == 2
    assert stored.frontier_remaining == 3
    assert stored.elapsed_seconds == pytest.approx(6.25)


def test_the_scope_options_are_kept_so_the_counters_can_be_read(
    repository: SQLiteCrawlRepository,
) -> None:
    session = make_session()
    repository.start_crawl(session)
    repository.finish_crawl(session, make_report(session))

    stored = repository.stored_crawl("crawl-1")

    assert stored is not None
    assert stored.max_depth == 2
    assert stored.max_pages == 20
    assert stored.same_domain is True
    assert stored.include_subdomains is False


@pytest.mark.parametrize(
    "state", [CrawlState.COMPLETED, CrawlState.PAGE_LIMIT, CrawlState.INTERRUPTED]
)
def test_every_terminal_state_round_trips(
    repository: SQLiteCrawlRepository, state: CrawlState
) -> None:
    session = make_session()
    repository.start_crawl(session)

    repository.finish_crawl(session, make_report(session, state=state))

    stored = repository.stored_crawl("crawl-1")
    assert stored is not None
    assert stored.state is state


def test_a_crawl_that_never_finished_keeps_its_started_row(
    repository: SQLiteCrawlRepository,
) -> None:
    """A process killed mid-run leaves an honest record of what happened."""
    repository.start_crawl(make_session())

    stored = repository.stored_crawl("crawl-1")

    assert stored is not None
    assert stored.finished_at is None
    assert stored.pages_visited == 0


def test_restarting_a_session_clears_its_previous_ending(
    repository: SQLiteCrawlRepository,
) -> None:
    session = make_session()
    repository.start_crawl(session)
    repository.finish_crawl(session, make_report(session))

    repository.start_crawl(session)

    stored = repository.stored_crawl("crawl-1")
    assert stored is not None
    assert stored.finished_at is None
    assert stored.state is CrawlState.RUNNING


def test_an_unknown_session_is_reported_as_missing(
    repository: SQLiteCrawlRepository,
) -> None:
    assert repository.stored_crawl("nope") is None


def test_crawls_are_listed_most_recent_first(repository: SQLiteCrawlRepository) -> None:
    older = make_session(session_id="crawl-0", started_at=datetime(2026, 8, 6, tzinfo=UTC))
    repository.start_crawl(older)
    repository.start_crawl(make_session())

    listed = repository.stored_crawls()

    assert [crawl.session_id for crawl in listed] == ["crawl-1", "crawl-0"]


def test_initializing_twice_is_harmless(tmp_path: Path) -> None:
    store = SQLiteCrawlRepository(SQLiteDatabase(tmp_path / "crawls.db"))

    store.initialize()
    store.initialize()

    assert store.stored_crawls() == ()


def test_a_crawl_shares_its_identifier_with_its_discovery_session(
    repository: SQLiteCrawlRepository,
) -> None:
    """One key joins crawl_sessions to scan_sessions and to discovered_urls."""
    session = make_session()

    repository.start_crawl(session)

    stored = repository.stored_crawl(session.scan_session.session_id)
    assert stored is not None


# --- what must never be written ----------------------------------------------


def test_a_request_context_never_reaches_the_database(
    repository: SQLiteCrawlRepository, tmp_path: Path
) -> None:
    """The report can reach a context by traversal; the file must not hold it.

    This is the module where a credential would end up in a file, so the file
    itself is searched rather than the code trusted.
    """
    session = make_session(
        context=RequestContext.of(
            user_agent="MaxiCrawler/test",
            headers={"Authorization": "Bearer SuperSecretValue", "Cookie": "sid=SecretSession"},
        )
    )
    repository.start_crawl(session)
    repository.finish_crawl(session, make_report(session))

    written = (tmp_path / "crawls.db").read_bytes()

    assert b"SuperSecretValue" not in written
    assert b"SecretSession" not in written
    assert b"Authorization" not in written


def test_the_adapter_reads_no_request_context() -> None:
    """Asserted from the syntax tree, so widening it has to be deliberate."""
    source = Path("src/maxicrawler/database/crawls.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

    assert "context" not in attributes
    assert "headers" not in attributes
    assert "user_agent" not in attributes


def test_the_schema_has_no_column_for_a_credential() -> None:
    from maxicrawler.database.crawls import SCHEMA

    schema = " ".join(SCHEMA).lower()

    for forbidden in ("cookie", "header", "auth", "token", "password", "proxy"):
        assert forbidden not in schema


def test_the_attempt_count_round_trips(repository: SQLiteCrawlRepository) -> None:
    """It explains a ceiling that pages_visited plus pages_failed does not."""
    session = make_session()
    repository.start_crawl(session)

    repository.finish_crawl(session, make_report(session))

    stored = repository.stored_crawl("crawl-1")
    assert stored is not None
    assert stored.pages_attempted == 18


# --- migrating a database from an earlier release -----------------------------

SPRINT_9_SCHEMA = """
CREATE TABLE crawl_sessions (
    session_id TEXT PRIMARY KEY,
    seed_url TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    state TEXT NOT NULL,
    max_depth INTEGER NOT NULL DEFAULT 0,
    max_pages INTEGER NOT NULL DEFAULT 0,
    same_domain INTEGER NOT NULL DEFAULT 0,
    include_subdomains INTEGER NOT NULL DEFAULT 0,
    pages_visited INTEGER NOT NULL DEFAULT 0,
    pages_failed INTEGER NOT NULL DEFAULT 0,
    pages_skipped INTEGER NOT NULL DEFAULT 0,
    links_discovered INTEGER NOT NULL DEFAULT 0,
    max_depth_reached INTEGER NOT NULL DEFAULT 0,
    frontier_remaining INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds REAL NOT NULL DEFAULT 0.0
)
"""
"""``crawl_sessions`` exactly as the release that introduced it created it."""

SPRINT_9_COLUMNS = frozenset(
    {
        "session_id",
        "seed_url",
        "started_at",
        "finished_at",
        "state",
        "max_depth",
        "max_pages",
        "same_domain",
        "include_subdomains",
        "pages_visited",
        "pages_failed",
        "pages_skipped",
        "links_discovered",
        "max_depth_reached",
        "frontier_remaining",
        "elapsed_seconds",
    }
)


def make_old_database(path: Path, *, with_row: bool = True) -> SQLiteCrawlRepository:
    """Return a repository over a database written by the earlier release."""
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(SPRINT_9_SCHEMA)
        if with_row:
            connection.execute(
                "INSERT INTO crawl_sessions(session_id, seed_url, started_at, state, "
                "max_depth, max_pages, pages_visited, links_discovered) "
                "VALUES('older', 'https://older.test/', '2026-08-01T09:00:00+00:00', "
                "'completed', 2, 50, 7, 300)"
            )
    return SQLiteCrawlRepository(SQLiteDatabase(path))


def test_a_database_from_the_earlier_release_gains_the_new_column(tmp_path: Path) -> None:
    """The reported bug: `no such column: pages_attempted` on an existing file."""
    store = make_old_database(tmp_path / "maxicrawler.db")

    added = store.initialize()

    assert added == ("pages_attempted", "respect_robots", "below_seed")
    assert "pages_attempted" in store.database.table_columns("crawl_sessions")


def test_a_migrated_database_can_finish_a_crawl(tmp_path: Path) -> None:
    """Where the failure actually landed: at the end, writing the summary."""
    store = make_old_database(tmp_path / "maxicrawler.db")
    store.initialize()
    session = make_session()

    store.start_crawl(session)
    store.finish_crawl(session, make_report(session))

    stored = store.stored_crawl("crawl-1")
    assert stored is not None
    assert stored.pages_attempted == 18


def test_migrating_leaves_the_earlier_rows_readable(tmp_path: Path) -> None:
    store = make_old_database(tmp_path / "maxicrawler.db")
    store.initialize()

    stored = store.stored_crawl("older")

    assert stored is not None
    assert stored.seed_url == "https://older.test/"
    assert stored.pages_visited == 7
    assert stored.links_discovered == 300
    assert stored.pages_attempted == 0
    assert stored.respect_robots is True
    # False rather than true, unlike robots.txt above: a run from before the
    # column existed could not have been confined to a path, and a default that
    # claimed otherwise would put a restriction on the record that never ran.
    assert stored.below_seed is False


def test_migrating_twice_changes_nothing(tmp_path: Path) -> None:
    store = make_old_database(tmp_path / "maxicrawler.db")

    assert store.initialize() == ("pages_attempted", "respect_robots", "below_seed")
    assert store.initialize() == ()
    assert store.initialize() == ()


def test_a_fresh_database_needs_no_migration(tmp_path: Path) -> None:
    store = SQLiteCrawlRepository(SQLiteDatabase(tmp_path / "fresh.db"))

    assert store.initialize() == ()
    assert "pages_attempted" in store.database.table_columns("crawl_sessions")


def test_every_column_added_since_the_first_release_is_declared() -> None:
    """The guard against this bug returning.

    Adding a column to SCHEMA without declaring it in ADDED_COLUMNS fails here,
    rather than in an operator's crawl three weeks later.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteCrawlRepository(SQLiteDatabase(Path(directory) / "current.db"))
        store.initialize()
        current = store.database.table_columns("crawl_sessions")

    assert current == SPRINT_9_COLUMNS | set(ADDED_COLUMNS)


def test_every_added_column_carries_a_default() -> None:
    """Without one, ALTER TABLE refuses to add it to a table holding rows."""
    for name, definition in ADDED_COLUMNS.items():
        assert "DEFAULT" in definition.upper(), name
