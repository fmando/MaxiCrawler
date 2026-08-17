"""What the terminal says about a MuseScore worklist.

Pure rendering, so none of this needs a database or a folder. The one test that
looks like pedantry is not: every printed line has to be ASCII, because Windows
consoles default to cp1252, where an arrow is not a mangled character but an
unhandled UnicodeEncodeError. The command does not print oddly there — it stops.
"""

from datetime import UTC, datetime
from pathlib import Path

from maxicrawler.app.musescore import Arrival, Budget, Match, Today
from maxicrawler.cli.musescore import render_added, render_today
from maxicrawler.database.musescore import RequestState, StoredRequest

MONDAY = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
FOLDER = "/home/somebody/Downloads"


def request(number: str = "4217351", fmt: str = "pdf", *, title: str = "") -> StoredRequest:
    """Return one line of a worklist."""
    return StoredRequest(
        request_id="a" * 32,
        score_id=number,
        format=fmt,
        score_url=f"https://musescore.com/user/1/scores/{number}",
        title=title,
        state=RequestState.OFFERED,
        position=1,
        added_at=MONDAY,
        offered_at=MONDAY,
        settled_at=None,
        settled_day="",
        entry_key="",
        note="",
    )


def today(
    *, offered: tuple[StoredRequest, ...] = (), spent: int = 0, limit: int = 20, waiting: int = 0
) -> Today:
    """Return a day with *offered* on it."""
    return Today(
        budget=Budget(day="2026-08-17", limit=limit, spent=spent),
        offered=offered,
        waiting=waiting,
        returned=0,
    )


def arrival(name: str = "Hallelujah.pdf") -> Arrival:
    """Return one file found in the download folder."""
    return Arrival(path=Path(FOLDER) / name, format="pdf", size=1024, modified_at=MONDAY)


def test_the_allowance_comes_first() -> None:
    """It decides whether the rest is worth reading."""
    output = render_today(today(spent=3), (), folder=FOLDER)

    assert output.splitlines()[0] == "Allowance for 2026-08-17: 3 of 20 taken, 17 left"


def test_a_spent_day_says_so_rather_than_printing_a_list() -> None:
    output = render_today(today(offered=(request(),), spent=20), (), folder=FOLDER)

    assert "today's allowance is spent" in output


def test_an_empty_backlog_is_told_apart_from_a_spent_day() -> None:
    """Two reasons for an empty list, and different things to do about them."""
    output = render_today(today(), (), folder=FOLDER)

    assert "the backlog is empty" in output


def test_each_line_carries_the_address_to_open() -> None:
    output = render_today(today(offered=(request(),)), (), folder=FOLDER)

    assert "https://musescore.com/user/1/scores/4217351" in output


def test_a_placed_arrival_carries_the_command_that_keeps_it() -> None:
    match = Match(arrival=arrival(), request=request())

    output = render_today(today(offered=(request(),)), (match,), folder=FOLDER)

    assert "--keep " + "a" * 32 in output


def test_an_unplaced_arrival_keeps_its_reason() -> None:
    """ "MaxiCrawler ignored my file" reads worse than "two lines could be this pdf"."""
    match = Match(arrival=arrival(), request=None, reason="2 lines could be this pdf")

    output = render_today(today(), (match,), folder=FOLDER)

    assert "2 lines could be this pdf" in output


def test_the_folder_being_read_is_named() -> None:
    output = render_today(today(), (), folder=FOLDER)

    assert FOLDER in output


def test_adding_nothing_new_says_so() -> None:
    assert "already on the list" in render_added(0, formats=("pdf",))


def test_adding_names_the_renderings_it_queued() -> None:
    assert "(pdf, mscz)" in render_added(4, formats=("pdf", "mscz"))


def test_every_printed_line_is_ascii() -> None:
    """cp1252 cannot encode an arrow, and the failure is a crash rather than a smudge.

    Guarding the rendered output rather than the source, so the prose in these
    docstrings stays free to use whatever punctuation reads best.
    """
    match = Match(arrival=arrival(), request=request(title="Study in Four Bars"))
    unplaced = Match(arrival=arrival("other.pdf"), request=None, reason="nothing waiting")
    rendered = [
        render_today(today(offered=(request(),), waiting=3), (match, unplaced), folder=FOLDER),
        render_today(today(spent=20), (), folder=FOLDER),
        render_today(today(), (), folder=FOLDER),
        render_added(0, formats=("pdf",)),
        render_added(2, formats=("pdf", "mscz")),
    ]

    for output in rendered:
        output.encode("cp1252")
