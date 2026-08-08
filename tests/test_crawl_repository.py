"""Tests for persisting the summary of a crawl."""

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest

from maxicrawler.crawler import DiscoverySummary, PluginUsage
from maxicrawler.database import SQLiteCrawlRepository, SQLiteDatabase
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
    assert stored.options.max_depth == 2
    assert stored.options.max_pages == 20
    assert stored.options.same_domain is True
    assert stored.options.include_subdomains is False


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
