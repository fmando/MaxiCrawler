"""A worklist worked through in a browser, and what comes back from it.

The failures worth testing are the quiet ones. A day that looks spent when it
is not costs a day. A file matched to the wrong line files music under the
wrong name in a library meant to be kept. A folder scanned twice that settles
the same line twice charges two days for one file. None of those announces
itself.
"""

import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from maxicrawler.app.musescore import (
    Arrival,
    ArrivalRefusedError,
    OutsideDownloadsError,
    WorklistService,
    day_of,
)
from maxicrawler.config import Settings
from maxicrawler.database import SQLiteDatabase, SQLiteRequestQueue
from maxicrawler.database.musescore import RequestState, StoredRequest
from maxicrawler.library import Library

MONDAY = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
TUESDAY = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
SCORE = "https://musescore.com/user/21965011/scores/4217351"
OTHER = "https://musescore.com/user/1/scores/999"
PDF = b"%PDF-1.4 a score\n" + b"x" * 4096
"""A payload the size sheet music actually is.

Measured rather than invented: a two-page score off that host is a 36 kB PDF
beside a 19 kB MSCZ. A forty-byte fixture would pass every test here and tell
nobody that the shipped 100 kB floor refuses the real thing.
"""


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[WorklistService, SQLiteRequestQueue, Path]:
    """Return a service over a throwaway library, queue, and download folder."""
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    settings = Settings(
        library_path=tmp_path / "library",
        musescore_downloads=str(downloads),
        musescore_daily_limit=4,
        musescore_formats=("pdf", "mscz"),
    )
    library = Library(settings.library_path)
    library.initialize()
    queue = SQLiteRequestQueue(SQLiteDatabase(tmp_path / "worklist.db"))
    queue.initialize()
    return WorklistService(settings, queue, library=library), queue, downloads


def service(workspace: tuple[WorklistService, SQLiteRequestQueue, Path]) -> WorklistService:
    return workspace[0]


def labels(requests: tuple[StoredRequest, ...]) -> list[str]:
    return [f"{request.score_id}.{request.format}" for request in requests]


def drop_file(folder: Path, name: str, payload: bytes = PDF) -> Path:
    """Write *name* into *folder* as if a browser had put it there."""
    path = folder / name
    path.write_bytes(payload)
    return path


def stamp(path: Path, moment: datetime) -> Path:
    """Give *path* the modification time *moment*.

    Set on both sides of a cutoff test rather than leaning on the wall clock:
    a fixture dated in a fixed 2026 and a file dated "now" compare differently
    depending on when the suite is run, which is a test that fails for reasons
    that have nothing to do with the code.
    """
    seconds = moment.timestamp()
    os.utime(path, (seconds, seconds))
    return path


# --- where a day begins ------------------------------------------------------


def test_midnight_reset_makes_a_day_just_the_date() -> None:
    """The case that should need no thought."""
    assert day_of(datetime(2026, 8, 17, 23, 59, tzinfo=UTC), reset_hour=0) == "2026-08-17"


def test_before_the_reset_hour_still_belongs_to_yesterday() -> None:
    """ "The allowance has not come back yet" is what an early morning means."""
    early = datetime(2026, 8, 18, 2, 0, tzinfo=UTC)

    assert day_of(early, reset_hour=6) == "2026-08-17"
    assert day_of(early.replace(hour=7), reset_hour=6) == "2026-08-18"


# --- building the list -------------------------------------------------------


def test_a_score_becomes_one_line_per_wanted_rendering(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    """The allowance is spent per download, so each rendering is its own line."""
    added = service(workspace).add([SCORE], now=MONDAY)

    assert labels(added) == ["4217351.pdf", "4217351.mscz"]


def test_anything_that_is_not_a_score_is_ignored_rather_than_refused(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    """A saved page holds forty other links, and that is normal input."""
    added = service(workspace).add(
        ["https://example.org/", "https://musescore.com/sheetmusic/piano", SCORE],
        now=MONDAY,
    )

    assert labels(added) == ["4217351.pdf", "4217351.mscz"]


def test_the_same_score_twice_in_one_list_is_queued_once(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    added = service(workspace).add([SCORE, f"{SCORE}/embed"], now=MONDAY)

    assert labels(added) == ["4217351.pdf", "4217351.mscz"]


# --- the day -----------------------------------------------------------------


def test_today_offers_up_to_the_allowance_and_no_further(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    worklist = service(workspace)
    worklist.add([SCORE, OTHER, "https://musescore.com/user/1/scores/1000"], now=MONDAY)

    today = worklist.today(now=MONDAY)

    assert len(today.offered) == 4
    assert today.budget.remaining == 4
    assert today.waiting == 2


def test_asking_twice_in_one_day_does_not_offer_twice(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    """A page reloaded is not a new day."""
    worklist = service(workspace)
    worklist.add([SCORE, OTHER], now=MONDAY)
    worklist.today(now=MONDAY)

    again = worklist.today(now=MONDAY)

    assert len(again.offered) == 4
    assert again.returned == 0


def test_yesterday_s_unclaimed_offers_come_back_today(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    worklist = service(workspace)
    worklist.add([SCORE, OTHER], now=MONDAY)
    worklist.today(now=MONDAY)

    today = worklist.today(now=TUESDAY)

    assert today.returned == 4
    assert len(today.offered) == 4


def test_the_list_is_topped_up_rather_than_left_short(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    """Two of four done should leave two more to do, not an empty afternoon."""
    worklist = service(workspace)
    worklist.add([SCORE, OTHER], now=MONDAY)
    first = worklist.today(now=MONDAY)
    drop_file(workspace[2], "one.pdf")
    worklist.store(first.offered[0].request_id, workspace[2] / "one.pdf", now=MONDAY)

    later = worklist.today(now=MONDAY)

    assert later.budget.spent == 1
    assert len(later.offered) == 3


def test_a_spent_day_offers_nothing_more(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    worklist = service(workspace)
    worklist.add([SCORE, OTHER], now=MONDAY)
    today = worklist.today(now=MONDAY)
    for index, request in enumerate(today.offered):
        path = drop_file(workspace[2], f"file-{index}.pdf")
        worklist.store(request.request_id, path, now=MONDAY)

    tomorrow_morning = worklist.today(now=MONDAY.replace(hour=23))

    assert tomorrow_morning.budget.exhausted is True
    assert tomorrow_morning.offered == ()


# --- what arrived ------------------------------------------------------------


def test_only_the_wanted_renderings_are_noticed(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    """A download folder holds a person's whole life, almost none of it music."""
    worklist, _, downloads = workspace
    drop_file(downloads, "score.pdf")
    drop_file(downloads, "tax-return.xlsx")
    drop_file(downloads, "holiday.jpg")

    assert [arrival.path.name for arrival in worklist.arrivals()] == ["score.pdf"]


def test_a_missing_download_folder_is_not_a_fault(tmp_path: Path) -> None:
    """Not having downloaded anything yet is an ordinary state."""
    settings = Settings(
        library_path=tmp_path / "library", musescore_downloads=str(tmp_path / "nowhere")
    )
    queue = SQLiteRequestQueue(SQLiteDatabase(tmp_path / "worklist.db"))
    queue.initialize()

    assert WorklistService(settings, queue).arrivals() == ()


def test_files_from_before_the_list_was_handed_out_are_not_arrivals(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    """Yesterday's downloads must not settle today's lines."""
    worklist, _, downloads = workspace
    stamp(drop_file(downloads, "old.pdf"), datetime(2026, 8, 1, tzinfo=UTC))
    stamp(drop_file(downloads, "new.pdf"), MONDAY.replace(hour=11))

    arrivals = worklist.arrivals(since=MONDAY)

    assert [arrival.path.name for arrival in arrivals] == ["new.pdf"]


# --- matching ----------------------------------------------------------------


def test_one_arrival_and_one_offer_belong_to_each_other(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    worklist, _, downloads = workspace
    worklist.add([SCORE], now=MONDAY)
    today = worklist.today(now=MONDAY)
    drop_file(downloads, "Hallelujah.pdf")

    matches = worklist.match(worklist.arrivals(), today.offered)

    assert matches[0].request is not None
    assert matches[0].request.format == "pdf"


def test_two_of_each_is_a_guess_and_is_refused(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    """Filing music under the wrong name is worse than asking."""
    worklist, _, downloads = workspace
    worklist.add([SCORE, OTHER], now=MONDAY)
    today = worklist.today(now=MONDAY)
    drop_file(downloads, "one.pdf")
    drop_file(downloads, "two.pdf")

    matches = worklist.match(worklist.arrivals(), today.offered)

    assert all(match.request is None for match in matches)
    assert "could be this pdf" in matches[0].reason


def test_a_title_settles_what_would_otherwise_be_ambiguous(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    """The one case where two candidates are not a guess."""
    worklist, queue, downloads = workspace
    worklist.add([SCORE, OTHER], now=MONDAY)
    today = worklist.today(now=MONDAY)
    titled = [request for request in today.offered if request.format == "pdf"]
    named = replace(titled[0], title="Study in Four Bars")
    drop_file(downloads, "Study in Four Bars.pdf")

    matches = worklist.match(worklist.arrivals(), (named, titled[1]))

    assert matches[0].request is named


def test_an_arrival_nothing_is_waiting_for_says_so(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    worklist, _, downloads = workspace
    drop_file(downloads, "stray.pdf")

    matches = worklist.match(worklist.arrivals(), ())

    assert matches[0].request is None
    assert "no pdf is waiting" in matches[0].reason


# --- crossing a line off -----------------------------------------------------


def test_an_arrived_file_lands_in_the_library(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    worklist, _, downloads = workspace
    worklist.add([SCORE], now=MONDAY)
    today = worklist.today(now=MONDAY)
    path = drop_file(downloads, "Hallelujah.pdf")

    stored = worklist.store(today.offered[0].request_id, path, now=MONDAY)

    assert stored is not None
    assert stored.state is RequestState.STORED
    entry = worklist._library.entry(worklist.reference(stored))
    assert entry.is_complete() is True


def test_the_downloaded_file_is_left_where_the_browser_put_it(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    """A program that tidied somebody's Downloads folder is one nobody should run."""
    worklist, _, downloads = workspace
    worklist.add([SCORE], now=MONDAY)
    today = worklist.today(now=MONDAY)
    path = drop_file(downloads, "Hallelujah.pdf")

    worklist.store(today.offered[0].request_id, path, now=MONDAY)

    assert path.exists()
    assert path.read_bytes() == PDF


def test_storing_the_same_line_twice_spends_one_day_not_two(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    """A folder scanned twice reports one arrival more than once."""
    worklist, _, downloads = workspace
    worklist.add([SCORE], now=MONDAY)
    today = worklist.today(now=MONDAY)
    path = drop_file(downloads, "Hallelujah.pdf")
    worklist.store(today.offered[0].request_id, path, now=MONDAY)

    again = worklist.store(today.offered[0].request_id, path, now=MONDAY)

    assert again is None
    assert worklist.budget(now=MONDAY).spent == 1


def test_a_dropped_line_spends_nothing(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    worklist = service(workspace)
    worklist.add([SCORE], now=MONDAY)
    today = worklist.today(now=MONDAY)

    worklist.drop(today.offered[0].request_id, now=MONDAY, note="wrong arrangement")

    assert worklist.budget(now=MONDAY).spent == 0


def test_the_stored_entry_is_indistinguishable_from_a_fetched_one(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    """Marking a hand-carried file as lesser would make the library two libraries."""
    worklist, _, downloads = workspace
    worklist.add([SCORE], now=MONDAY)
    today = worklist.today(now=MONDAY)
    path = drop_file(downloads, "Hallelujah.pdf")

    stored = worklist.store(today.offered[0].request_id, path, now=MONDAY)

    assert stored is not None
    record = worklist._library.entry(worklist.reference(stored)).read()
    assert record is not None
    assert record.is_complete is True
    assert record.content is not None
    assert record.content.size == len(PDF)
    assert record.source_url == SCORE


def test_a_file_under_the_floor_is_not_stored(tmp_path: Path) -> None:
    """The same refusal a transfer gets, in this service's own words.

    The downloader's exception does not travel: a client of this service must
    not have to name one to handle one, and the web interface is forbidden
    from importing that package at all.
    """
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    settings = Settings(
        library_path=tmp_path / "library",
        musescore_downloads=str(downloads),
        min_download_size=1024,
        musescore_formats=("pdf",),
    )
    library = Library(settings.library_path)
    library.initialize()
    queue = SQLiteRequestQueue(SQLiteDatabase(tmp_path / "worklist.db"))
    queue.initialize()
    worklist = WorklistService(settings, queue, library=library)
    worklist.add([SCORE], now=MONDAY)
    today = worklist.today(now=MONDAY)
    path = drop_file(downloads, "tiny.pdf", b"nope")

    with pytest.raises(ArrivalRefusedError, match="minimum download size"):
        worklist.store(today.offered[0].request_id, path, now=MONDAY)


def test_a_file_outside_the_download_folder_is_refused(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path], tmp_path: Path
) -> None:
    """The page that settles a line names a file, and the page has no sign-in.

    Without this, anybody who can reach the port could copy any readable file
    on the machine into the library. The download folder is the whole of what
    this feature needs to read, so it is the whole of what it may read.
    """
    worklist = service(workspace)
    worklist.add([SCORE], now=MONDAY)
    today = worklist.today(now=MONDAY)
    elsewhere = tmp_path / "private.pdf"
    elsewhere.write_bytes(PDF)

    with pytest.raises(OutsideDownloadsError):
        worklist.store(today.offered[0].request_id, elsewhere, now=MONDAY)


def test_a_path_walking_out_of_the_folder_is_refused(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path], tmp_path: Path
) -> None:
    """Judged by where it lands, not by how it was spelled."""
    worklist, _, downloads = workspace
    elsewhere = tmp_path / "private.pdf"
    elsewhere.write_bytes(PDF)

    with pytest.raises(OutsideDownloadsError):
        worklist.require_arrival(downloads / ".." / "private.pdf")


def test_a_folder_is_not_a_file(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    worklist, _, downloads = workspace
    (downloads / "a-folder.pdf").mkdir()

    with pytest.raises(OutsideDownloadsError):
        worklist.require_arrival(downloads / "a-folder.pdf")


def test_an_unknown_line_cannot_be_stored(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    worklist, _, downloads = workspace
    path = drop_file(downloads, "stray.pdf")

    assert worklist.store("nothing-like-this", path, now=MONDAY) is None


def test_an_arrival_knows_its_own_stem(tmp_path: Path) -> None:
    arrival = Arrival(
        path=tmp_path / "Study in Four Bars.pdf", format="pdf", size=1, modified_at=MONDAY
    )

    assert arrival.stem == "Study in Four Bars"


# --- what a review reports ---------------------------------------------------


def test_an_empty_worklist_reports_no_arrivals_at_all(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    """The bug this method exists for, and the state a page is first read in.

    A download folder holds years of invoices, magazines and tax reports. With
    nothing owed there is no moment to compare against, and falling back to no
    filter listed every PDF a person owns -- each one annotated, uselessly, as
    something no line was waiting for.
    """
    worklist, _, downloads = workspace
    for name in ("invoice-2021.pdf", "magazine.pdf", "tax-return.pdf"):
        drop_file(downloads, name)

    seen = worklist.review(now=MONDAY)

    assert seen.today.offered == ()
    assert seen.matches == ()


def test_a_review_reports_what_arrived_for_what_is_owed(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    worklist, _, downloads = workspace
    worklist.add([SCORE], now=MONDAY)
    seen = worklist.review(now=MONDAY)
    stamp(drop_file(downloads, "Hallelujah.pdf"), MONDAY.replace(hour=11))

    seen = worklist.review(now=MONDAY)

    assert len(seen.today.offered) == 2
    assert [match.arrival.path.name for match in seen.matches] == ["Hallelujah.pdf"]


def test_a_review_ignores_files_older_than_the_list(
    workspace: tuple[WorklistService, SQLiteRequestQueue, Path],
) -> None:
    """Years of unrelated downloads sit in that folder and settle nothing."""
    worklist, _, downloads = workspace
    stamp(drop_file(downloads, "invoice-2021.pdf"), datetime(2021, 3, 1, tzinfo=UTC))
    worklist.add([SCORE], now=MONDAY)
    worklist.review(now=MONDAY)

    seen = worklist.review(now=MONDAY)

    assert seen.matches == ()


def test_a_score_sized_file_is_kept_though_it_is_under_the_shipped_floor(
    tmp_path: Path,
) -> None:
    """Sheet music is small, and the global floor was guessed at rather than measured.

    Measured against a real download folder, a two-page score is a 36 kB PDF
    and its MSCZ is 19 kB. `min_download_size` ships at 100 kB, so honouring it
    here would have refused every score this feature exists for. That setting
    answers a different question -- bulk junk nobody chose -- and an arrival was
    chosen by a person in their own browser.
    """
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    settings = Settings(
        library_path=tmp_path / "library",
        musescore_downloads=str(downloads),
        musescore_formats=("pdf",),
    )
    assert settings.min_download_size == 100_000
    library = Library(settings.library_path)
    library.initialize()
    queue = SQLiteRequestQueue(SQLiteDatabase(tmp_path / "worklist.db"))
    queue.initialize()
    worklist = WorklistService(settings, queue, library=library)
    worklist.add([SCORE], now=MONDAY)
    today = worklist.today(now=MONDAY)
    score_sized = drop_file(downloads, "Hallelujah.pdf", b"%PDF-1.4" + b"x" * 36_000)

    stored = worklist.store(today.offered[0].request_id, score_sized, now=MONDAY)

    assert stored is not None
    assert stored.state is RequestState.STORED


def test_an_empty_file_is_still_refused(tmp_path: Path) -> None:
    """ "Is this worth keeping" is answered by a person; "is this a file" is not."""
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    settings = Settings(
        library_path=tmp_path / "library",
        musescore_downloads=str(downloads),
        musescore_formats=("pdf",),
    )
    library = Library(settings.library_path)
    library.initialize()
    queue = SQLiteRequestQueue(SQLiteDatabase(tmp_path / "worklist.db"))
    queue.initialize()
    worklist = WorklistService(settings, queue, library=library)
    worklist.add([SCORE], now=MONDAY)
    today = worklist.today(now=MONDAY)
    truncated = drop_file(downloads, "Hallelujah.pdf", b"")

    with pytest.raises(ArrivalRefusedError):
        worklist.store(today.offered[0].request_id, truncated, now=MONDAY)
