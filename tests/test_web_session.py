"""Tests for the crawl session, its options and its state."""

from datetime import UTC, datetime
from time import monotonic

import pytest

from maxicrawler.domain import ScanSession
from maxicrawler.web.session import (
    DEFAULT_MAX_PAGES,
    CrawlControl,
    CrawlOptions,
    CrawlSession,
    CrawlState,
    RequestContext,
)


def make_session(**kwargs: object) -> CrawlSession:
    """Return a crawl session over example.test."""
    options: dict[str, object] = {
        "session_id": "crawl-1",
        "seed_url": "https://example.test/",
        "started_at": datetime(2026, 8, 7, tzinfo=UTC),
    }
    options.update(kwargs)
    return CrawlSession(**options)  # type: ignore[arg-type]


# --- options -----------------------------------------------------------------


def test_a_crawl_is_not_recursive_by_default() -> None:
    assert CrawlOptions().max_depth == 0
    assert CrawlOptions().is_recursive is False


def test_a_depth_above_zero_makes_a_crawl_recursive() -> None:
    assert CrawlOptions(max_depth=2).is_recursive is True


def test_the_domain_restriction_is_off_by_default() -> None:
    """Hunting for share links means following Mega and Pixeldrain off-site.

    Restricting by default would quietly break that workflow, so depth and the
    page ceiling are what bound a crawl instead.
    """
    assert CrawlOptions().same_domain is False
    assert CrawlOptions().include_subdomains is False


def test_the_page_ceiling_has_a_documented_default() -> None:
    assert CrawlOptions().max_pages == DEFAULT_MAX_PAGES


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_depth": -1}, "max_depth must not be negative"),
        ({"max_pages": 0}, "max_pages must be at least 1"),
    ],
)
def test_impossible_options_are_refused(kwargs: dict[str, int], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        CrawlOptions(**kwargs)  # type: ignore[arg-type]


def test_options_are_immutable() -> None:
    options = CrawlOptions()

    with pytest.raises(AttributeError):
        options.max_depth = 3  # type: ignore[misc]


# --- the request context -----------------------------------------------------


def test_a_context_carries_the_user_agent() -> None:
    assert RequestContext(user_agent="MaxiCrawler/test").user_agent == "MaxiCrawler/test"


def test_a_context_orders_its_headers_so_it_stays_hashable() -> None:
    context = RequestContext.of(
        user_agent="MaxiCrawler/test",
        headers={"X-Trace": "abc", "Accept-Language": "de"},
    )

    assert context.headers == (("Accept-Language", "de"), ("X-Trace", "abc"))
    assert hash(context)


def test_a_context_returns_its_headers_as_a_mapping() -> None:
    context = RequestContext.of(user_agent="a", headers={"X-Trace": "abc"})

    assert context.header_map() == {"X-Trace": "abc"}


def test_a_context_without_headers_is_empty() -> None:
    assert RequestContext.of(user_agent="a").headers == ()


# --- the session -------------------------------------------------------------


def test_a_session_names_its_seed_and_its_options() -> None:
    session = make_session(options=CrawlOptions(max_depth=2, same_domain=True))

    assert session.seed_url == "https://example.test/"
    assert session.options.max_depth == 2
    assert session.options.same_domain is True


def test_a_session_shares_its_identifier_with_its_discovery_session() -> None:
    """One crawl is one row in each table, joined without a second key."""
    session = make_session()

    scan = session.scan_session

    assert isinstance(scan, ScanSession)
    assert scan.session_id == session.session_id
    assert scan.started_at == session.started_at


def test_a_session_has_workable_defaults() -> None:
    session = make_session()

    assert session.options == CrawlOptions()
    assert session.context.user_agent == "MaxiCrawler"


def test_a_session_is_immutable() -> None:
    session = make_session()

    with pytest.raises(AttributeError):
        session.seed_url = "https://elsewhere.test/"  # type: ignore[misc]


def test_a_session_holds_its_context_without_reading_it() -> None:
    """The crawler must not know how a request is authenticated.

    The context is data the fetcher applies; the behaviour that would perform
    a login belongs to a fetcher decorator. This only pins down that the
    session carries it untouched.
    """
    context = RequestContext.of(user_agent="a", headers={"Authorization": "Bearer x"})

    session = make_session(context=context)

    assert session.context is context


# --- state and control -------------------------------------------------------


def test_a_control_starts_pending_and_unstopped() -> None:
    control = CrawlControl()

    assert control.state is CrawlState.PENDING
    assert control.stop_requested is False


def test_requesting_a_stop_is_observable() -> None:
    control = CrawlControl()

    control.request_stop()

    assert control.stop_requested is True


def test_requesting_a_stop_twice_is_harmless() -> None:
    control = CrawlControl()
    control.request_stop()
    control.request_stop()

    assert control.stop_requested is True


def test_a_wait_returns_at_once_when_a_stop_was_asked_for() -> None:
    """What makes politeness interruptible.

    A crawl waiting out a thirty-second Crawl-delay would otherwise hold a
    shutdown open for thirty seconds.
    """
    control = CrawlControl()
    control.request_stop()

    started = monotonic()
    control.wait(30.0)

    assert monotonic() - started < 1.0


def test_a_wait_without_a_stop_actually_waits() -> None:
    """Loosely, because a platform timer may fire a little early."""
    control = CrawlControl()

    started = monotonic()
    control.wait(0.05)

    assert monotonic() - started >= 0.03


def test_the_live_state_can_be_followed() -> None:
    control = CrawlControl()

    control.state = CrawlState.RUNNING

    assert control.state is CrawlState.RUNNING


@pytest.mark.parametrize(
    ("state", "finished"),
    [
        (CrawlState.PENDING, False),
        (CrawlState.RUNNING, False),
        (CrawlState.COMPLETED, True),
        (CrawlState.PAGE_LIMIT, True),
        (CrawlState.INTERRUPTED, True),
    ],
)
def test_terminal_states_are_recognized(state: CrawlState, finished: bool) -> None:
    assert state.is_finished is finished


def test_a_state_renders_as_its_value() -> None:
    assert str(CrawlState.PAGE_LIMIT) == "page_limit"


def test_a_control_repr_names_state_and_stop_flag() -> None:
    control = CrawlControl()

    assert "pending" in repr(control)
    assert "stop_requested=False" in repr(control)
