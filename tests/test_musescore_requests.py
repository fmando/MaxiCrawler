"""A worklist that outlives the process, and the day it is measured against.

The tests worth having here are about *not* losing and *not* double-counting.
A backlog spanning weeks is only useful if adding to it twice is harmless, if
an unfinished day comes back tomorrow, and if one file cannot spend two days of
an allowance. Each of those is a way to be quietly wrong for a fortnight before
anybody notices.
"""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from maxicrawler.database import SQLiteDatabase, SQLiteRequestQueue
from maxicrawler.database.musescore import (
    ADDED_COLUMNS,
    RequestState,
    ScoreRequest,
    StoredRequest,
)

MONDAY = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
TUESDAY = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)

FIRST_RELEASE_COLUMNS = {
    "request_id",
    "score_id",
    "format",
    "score_url",
    "title",
    "state",
    "position",
    "added_at",
    "offered_at",
    "settled_at",
    "settled_day",
    "entry_key",
    "note",
}
"""The shape the table was first released with.

Written out rather than read from the schema on purpose: a test that derived
this from the code under test would agree with any change, including the one it
exists to catch.
"""


@pytest.fixture
def queue(tmp_path: Path) -> SQLiteRequestQueue:
    """Return an initialized queue over a throwaway database."""
    store = SQLiteRequestQueue(SQLiteDatabase(tmp_path / "worklist.db"))
    store.initialize()
    return store


def score(number: str, fmt: str = "pdf", *, title: str = "") -> ScoreRequest:
    """Return a request for one rendering of a score."""
    return ScoreRequest(
        score_id=number,
        format=fmt,
        score_url=f"https://musescore.com/user/1/scores/{number}",
        title=title,
    )


def labels(requests: tuple[StoredRequest, ...]) -> list[str]:
    """Return the score-and-format identity of each request."""
    return [f"{request.score_id}.{request.format}" for request in requests]


# --- adding ------------------------------------------------------------------


def test_a_request_starts_out_waiting(queue: SQLiteRequestQueue) -> None:
    added = queue.add([score("1")], now=MONDAY)

    assert labels(added) == ["1.pdf"]
    assert added[0].state is RequestState.WAITING
    assert added[0].added_at == MONDAY


def test_the_same_score_in_two_formats_is_two_requests(queue: SQLiteRequestQueue) -> None:
    """The allowance is spent per download, so each rendering is its own line."""
    added = queue.add([score("1", "pdf"), score("1", "mscz")], now=MONDAY)

    assert labels(added) == ["1.pdf", "1.mscz"]


def test_adding_the_same_collection_twice_adds_nothing(queue: SQLiteRequestQueue) -> None:
    """The ordinary way somebody adds to a list is to paste it again."""
    queue.add([score("1"), score("2")], now=MONDAY)

    again = queue.add([score("2"), score("3")], now=TUESDAY)

    assert labels(again) == ["3.pdf"]
    assert len(queue.requests()) == 3


def test_the_identity_is_the_score_rather_than_the_url(queue: SQLiteRequestQueue) -> None:
    """The same score is reachable under a vanity profile and a numeric one.

    Keyed on the URL, one piece of music would spend two days of an allowance.
    """
    queue.add([score("1")], now=MONDAY)
    vanity = ScoreRequest(
        score_id="1", format="pdf", score_url="https://musescore.com/somebody/scores/1"
    )

    assert queue.add([vanity], now=MONDAY) == ()


def test_adding_again_does_not_resurrect_what_was_dropped(queue: SQLiteRequestQueue) -> None:
    """Dropping is a decision, and pasting the list again is not a reversal of it."""
    added = queue.add([score("1")], now=MONDAY)
    queue.drop(added[0].request_id, now=MONDAY, note="not wanted")

    queue.add([score("1")], now=TUESDAY)

    assert queue.counts()[RequestState.DROPPED] == 1
    assert queue.counts()[RequestState.WAITING] == 0


def test_adding_again_does_not_re_offer_what_already_arrived(queue: SQLiteRequestQueue) -> None:
    added = queue.add([score("1")], now=MONDAY)
    queue.mark_stored(added[0].request_id, now=MONDAY, day="2026-08-17")

    queue.add([score("1")], now=TUESDAY)

    assert queue.counts()[RequestState.STORED] == 1
    assert queue.counts()[RequestState.WAITING] == 0


# --- offering ----------------------------------------------------------------


def test_offering_takes_the_oldest_first(queue: SQLiteRequestQueue) -> None:
    """A backlog drains in the order it was built, not in the database's order."""
    queue.add([score("1")], now=MONDAY)
    queue.add([score("2")], now=TUESDAY)

    offered = queue.offer(1, now=TUESDAY)

    assert labels(offered) == ["1.pdf"]


def test_one_batch_drains_in_the_order_it_was_pasted(queue: SQLiteRequestQueue) -> None:
    """Two hundred links pasted at once share a timestamp to the microsecond.

    Ordered by that alone, a batch would drain in whatever its row identifiers
    happened to sort as — which is to say randomly, discarding the order
    somebody put their own list in.
    """
    queue.add([score(str(number)) for number in range(1, 6)], now=MONDAY)

    offered = queue.offer(5, now=MONDAY)

    assert labels(offered) == ["1.pdf", "2.pdf", "3.pdf", "4.pdf", "5.pdf"]


def test_a_later_batch_queues_behind_an_earlier_one(queue: SQLiteRequestQueue) -> None:
    queue.add([score("1"), score("2")], now=MONDAY)
    queue.add([score("3")], now=MONDAY)

    offered = queue.offer(3, now=MONDAY)

    assert labels(offered) == ["1.pdf", "2.pdf", "3.pdf"]


def test_offering_stops_at_what_there_is(queue: SQLiteRequestQueue) -> None:
    queue.add([score("1")], now=MONDAY)

    assert len(queue.offer(20, now=MONDAY)) == 1


def test_offering_nothing_is_a_perfectly_good_answer(queue: SQLiteRequestQueue) -> None:
    """An empty allowance and an empty backlog are not errors."""
    assert queue.offer(0, now=MONDAY) == ()
    assert queue.offer(5, now=MONDAY) == ()


def test_an_offered_request_is_not_offered_twice(queue: SQLiteRequestQueue) -> None:
    queue.add([score("1"), score("2")], now=MONDAY)

    first = queue.offer(1, now=MONDAY)
    second = queue.offer(1, now=MONDAY)

    assert labels(first) == ["1.pdf"]
    assert labels(second) == ["2.pdf"]


def test_yesterday_s_unfinished_list_comes_back(queue: SQLiteRequestQueue) -> None:
    """Nothing was lost when a list was not worked through; it was not clicked.

    Left offered, a stale list would hide behind today's. Counted as spent, it
    would charge somebody for files they never got.
    """
    queue.add([score("1")], now=MONDAY)
    queue.offer(1, now=MONDAY)

    returned = queue.withdraw_offers(before_day="2026-08-18")

    assert returned == 1
    assert queue.counts()[RequestState.WAITING] == 1


def test_today_s_list_is_left_alone(queue: SQLiteRequestQueue) -> None:
    queue.add([score("1")], now=TUESDAY)
    queue.offer(1, now=TUESDAY)

    assert queue.withdraw_offers(before_day="2026-08-18") == 0
    assert queue.counts()[RequestState.OFFERED] == 1


# --- settling and the allowance ----------------------------------------------


def test_a_stored_file_spends_the_day_it_was_charged_to(queue: SQLiteRequestQueue) -> None:
    added = queue.add([score("1")], now=MONDAY)

    queue.mark_stored(added[0].request_id, now=MONDAY, day="2026-08-17", entry_key="abc")

    assert queue.spent_on("2026-08-17") == 1
    assert queue.spent_on("2026-08-18") == 0


def test_the_day_is_told_rather_than_derived(queue: SQLiteRequestQueue) -> None:
    """Where a day begins is a policy about somebody else's reset time.

    A row settled late on Monday may belong to Tuesday's allowance, and only
    the layer holding that policy can say so.
    """
    added = queue.add([score("1")], now=MONDAY)

    queue.mark_stored(added[0].request_id, now=MONDAY, day="2026-08-18")

    assert queue.spent_on("2026-08-18") == 1


def test_one_file_cannot_spend_two_days(queue: SQLiteRequestQueue) -> None:
    """A folder scanned twice, a page reloaded: arrival is reported more than once."""
    added = queue.add([score("1")], now=MONDAY)
    queue.mark_stored(added[0].request_id, now=MONDAY, day="2026-08-17")

    again = queue.mark_stored(added[0].request_id, now=TUESDAY, day="2026-08-18")

    assert again is None
    assert queue.spent_on("2026-08-18") == 0


def test_a_dropped_request_spends_nothing(queue: SQLiteRequestQueue) -> None:
    added = queue.add([score("1")], now=MONDAY)

    dropped = queue.drop(added[0].request_id, now=MONDAY, note="wrong arrangement")

    assert dropped is not None
    assert dropped.note == "wrong arrangement"
    assert queue.spent_on("2026-08-17") == 0


def test_settling_something_unknown_says_so_rather_than_inventing_it(
    queue: SQLiteRequestQueue,
) -> None:
    assert queue.mark_stored("nothing-like-this", now=MONDAY, day="2026-08-17") is None


def test_the_library_key_is_kept_so_the_file_can_be_found_again(
    queue: SQLiteRequestQueue,
) -> None:
    added = queue.add([score("1")], now=MONDAY)

    stored = queue.mark_stored(added[0].request_id, now=MONDAY, day="2026-08-17", entry_key="k-1")

    assert stored is not None
    assert stored.entry_key == "k-1"


# --- reading back ------------------------------------------------------------


def test_the_counts_name_every_state_including_the_empty_ones(
    queue: SQLiteRequestQueue,
) -> None:
    """A page showing "0 waiting" is clearer than a page showing nothing."""
    counts = queue.counts()

    assert set(counts) == set(RequestState)
    assert all(total == 0 for total in counts.values())


def test_a_worklist_survives_being_reopened(tmp_path: Path) -> None:
    """The whole point: the backlog is measured in weeks, not in processes."""
    path = tmp_path / "worklist.db"
    first = SQLiteRequestQueue(SQLiteDatabase(path))
    first.initialize()
    added = first.add([score("1"), score("2")], now=MONDAY)
    first.mark_stored(added[0].request_id, now=MONDAY, day="2026-08-17")

    second = SQLiteRequestQueue(SQLiteDatabase(path))
    second.initialize()

    assert second.spent_on("2026-08-17") == 1
    assert labels(second.by_state(RequestState.WAITING)) == ["2.pdf"]


def test_a_request_reads_back_as_it_was_written(queue: SQLiteRequestQueue) -> None:
    added = queue.add([score("1", "mscz", title="Study in Four Bars")], now=MONDAY)

    found = queue.request(added[0].request_id)

    assert found is not None
    assert found.title == "Study in Four Bars"
    assert found.label == "Study in Four Bars (mscz)"


def test_a_request_without_a_title_still_has_something_to_call_it(
    queue: SQLiteRequestQueue,
) -> None:
    """The title comes from a page this cannot fetch, so it is often absent."""
    added = queue.add([score("4217351")], now=MONDAY)

    assert added[0].label == "score 4217351 (pdf)"


# --- the schema --------------------------------------------------------------


def test_every_column_added_since_the_first_release_is_declared() -> None:
    """Adding a column to SCHEMA without declaring it in ADDED_COLUMNS fails here.

    Not three weeks later, in a backlog written by an earlier release.
    """
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteRequestQueue(SQLiteDatabase(Path(directory) / "current.db"))
        store.initialize()
        current = store.database.table_columns("musescore_requests")

    assert current == FIRST_RELEASE_COLUMNS | set(ADDED_COLUMNS)


def test_every_added_column_carries_a_default() -> None:
    """Without one, ALTER TABLE refuses to add it to a table holding rows."""
    for name, definition in ADDED_COLUMNS.items():
        assert "DEFAULT" in definition.upper(), name


def test_no_column_could_hold_a_session() -> None:
    """The file where a credential would end up on disk outliving its reason.

    Asserted rather than left to review: a column called anything like this
    arriving later should fail a test on the day it is written.
    """
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteRequestQueue(SQLiteDatabase(Path(directory) / "current.db"))
        store.initialize()
        columns = store.database.table_columns("musescore_requests")

    forbidden = ("cookie", "session", "credential", "token", "password", "auth")
    assert not [name for name in columns if any(word in name.lower() for word in forbidden)]
