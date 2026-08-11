"""Tests for the queue page: what it shows, and what its buttons do.

Everything here goes through HTTP, because the page's claim is that it works
as a set of plain forms. A test that called the queue directly would be
testing the queue again rather than the page over it.

The queue is paused for most of them. A page about *waiting* needs something
that stays waiting, and pausing is the honest way to arrange that — it is what
the button does, so the tests exercise the route as a side effect.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from starlette.testclient import TestClient
from test_api_pages import MEGA_KEY, BlockingProvider, client, make_provider

from maxicrawler.api.downloads import TransferQueue

FIRST = f"https://mega.nz/file/AaBbCcDd#{MEGA_KEY}"
SECOND = f"https://mega.nz/file/EeFfGgHh#{MEGA_KEY}"
THIRD = f"https://mega.nz/file/IiJjKkLl#{MEGA_KEY}"


def queue_of(test_client: TestClient) -> TransferQueue:
    """Return the queue the application under test is using."""
    queue: TransferQueue = test_client.app.state.downloads  # type: ignore[attr-defined]
    return queue


@contextmanager
def paused(tmp_path: Path) -> Iterator[TestClient]:
    """Yield a client whose queue takes nothing off itself until told to."""
    with client(tmp_path, provider=make_provider()) as test_client:
        response = test_client.post("/downloads/pause", data={"paused": "1"})
        assert response.status_code == 200  # followed the redirect to the page
        yield test_client


def submit(test_client: TestClient, url: str) -> str:
    """Queue *url* and return the download id it was given."""
    response = test_client.post("/downloads", data={"url": url}, follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[1]


def waiting_labels(body: str) -> list[str]:
    """Return the links listed as waiting, in the order the table has them."""
    table = body.split("<h2>\n      Waiting", 1)
    if len(table) == 1:
        return []
    rows = table[1].split("</table>", 1)[0]
    return re.findall(r'<a href="/downloads/[^"]+">([^<]+)</a>', rows)


# --- what it shows -------------------------------------------------------------


def test_an_untouched_queue_says_it_is_empty(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        body = test_client.get("/downloads").text

    assert "Nothing has been queued yet" in body
    assert waiting_labels(body) == []
    assert "Finished" not in body


def test_a_waiting_request_is_listed_with_its_place(tmp_path: Path) -> None:
    with paused(tmp_path) as test_client:
        submit(test_client, FIRST)
        submit(test_client, SECOND)

        body = test_client.get("/downloads").text

    assert waiting_labels(body) == [
        "https://mega.nz/file/AaBbCcDd",
        "https://mega.nz/file/EeFfGgHh",
    ]


def test_the_running_transfer_is_shown_in_full(tmp_path: Path) -> None:
    provider = BlockingProvider()
    with client(tmp_path, provider=provider) as test_client:
        submit(test_client, FIRST)
        assert provider.transferring.wait(timeout=10)

        body = test_client.get("/downloads").text
        provider.release.set()

    assert "download-progress" in body
    assert "Open this download" in body
    # The running transfer's own stream, which is what makes this page live.
    assert 'id="download-live"' in body


def test_a_quiet_queue_streams_nothing(tmp_path: Path) -> None:
    """No transfer, no frames to send: an idle page opens no connection."""
    with paused(tmp_path) as test_client:
        submit(test_client, FIRST)

        body = test_client.get("/downloads").text

    assert 'id="download-live"' not in body
    assert "download.js" not in body


# --- following a batch without reloading a page --------------------------------


def test_the_panels_can_be_asked_for_without_the_page_around_them(tmp_path: Path) -> None:
    """What makes following two hundred files cost two hundred small answers."""
    with paused(tmp_path) as test_client:
        submit(test_client, FIRST)

        response = test_client.get("/downloads?part=queue")

    assert response.status_code == 200
    assert "Left to do" in response.text
    assert waiting_labels(response.text) == ["https://mega.nz/file/AaBbCcDd"]
    # No chrome, because the page it is going into already has all of it.
    assert "<!DOCTYPE" not in response.text
    assert "<nav>" not in response.text


def test_the_panels_are_the_same_whether_they_arrive_alone_or_in_the_page(
    tmp_path: Path,
) -> None:
    """One template, so a swapped page and a reloaded one cannot drift apart."""
    with paused(tmp_path) as test_client:
        submit(test_client, FIRST)

        panels = test_client.get("/downloads?part=queue").text
        whole = test_client.get("/downloads").text

    assert panels.strip() in whole


def test_the_panels_are_never_served_from_a_cache(tmp_path: Path) -> None:
    """The one answer here asked for repeatedly at one URL while it changes."""
    with paused(tmp_path) as test_client:
        response = test_client.get("/downloads?part=queue")

    assert response.headers["cache-control"] == "no-store"


def test_a_part_nobody_knows_is_read_as_a_request_for_the_page(tmp_path: Path) -> None:
    """Read leniently, the way every parameter of this interface is."""
    with paused(tmp_path) as test_client:
        body = test_client.get("/downloads?part=sideways").text

    assert "<!DOCTYPE" in body
    assert "Left to do" in body


def test_the_running_page_says_where_to_ask_and_where_the_answer_goes(
    tmp_path: Path,
) -> None:
    """Decided by the server. The script reads three attributes and no more."""
    provider = BlockingProvider()
    with client(tmp_path, provider=provider) as test_client:
        submit(test_client, FIRST)
        assert provider.transferring.wait(timeout=10)

        body = test_client.get("/downloads").text
        provider.release.set()

    assert 'id="queue"' in body
    assert 'data-swap="/downloads?part=queue"' in body
    assert 'data-into="queue"' in body
    assert "data-stream=" in body


def test_a_downloads_own_page_asks_for_itself_rather_than_for_the_panels(
    tmp_path: Path,
) -> None:
    """Where the file went and the way to the library are worth a page load."""
    provider = BlockingProvider()
    with client(tmp_path, provider=provider) as test_client:
        download_id = submit(test_client, FIRST)
        assert provider.transferring.wait(timeout=10)

        body = test_client.get(f"/downloads/{download_id}").text
        provider.release.set()

    assert 'id="download-live"' in body
    assert "data-swap" not in body


def test_a_drained_queue_stops_watching_itself(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        submit(test_client, FIRST)

        body = wait_until_quiet(test_client)

    assert 'id="download-live"' not in body
    assert "download.js" not in body


def test_the_page_counts_what_is_left_and_what_arrived(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        submit(test_client, FIRST)
        wait_until_quiet(test_client)

        body = test_client.get("/downloads").text

    assert "Left to do" in body
    assert "Stored" in body
    assert "Finished" in body


def test_a_paused_queue_says_so_and_says_what_that_means(tmp_path: Path) -> None:
    with paused(tmp_path) as test_client:
        submit(test_client, FIRST)

        body = test_client.get("/downloads").text

    assert "paused" in body
    assert "nothing new is taken off it until you resume" in body
    assert ">Resume<" in body


def test_a_running_queue_offers_to_pause_rather_than_to_resume(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        body = test_client.get("/downloads").text

    assert ">Pause<" in body
    assert ">Resume<" not in body


def test_the_navigation_names_the_queue_and_marks_it(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        body = test_client.get("/downloads").text

    assert '<a href="/downloads" class="active">Downloads</a>' in body


# --- how far along the whole queue is ------------------------------------------


def test_the_page_says_how_much_of_the_queue_is_behind_it(tmp_path: Path) -> None:
    with paused(tmp_path) as test_client:
        submit(test_client, FIRST)
        removed = submit(test_client, SECOND)
        test_client.post(f"/downloads/{removed}/stop?back=/downloads")

        body = test_client.get("/downloads").text

    assert "1 of 2 finished" in body


def test_a_queue_nobody_has_used_shows_no_bar(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        body = test_client.get("/downloads").text

    assert "finished" not in body
    assert 'class="progress"' not in body


def test_the_page_offers_no_estimate_of_how_much_longer(tmp_path: Path) -> None:
    """A waiting request is uninspected: its size is not known, so no time is."""
    with paused(tmp_path) as test_client:
        submit(test_client, FIRST)
        submit(test_client, SECOND)

        body = test_client.get("/downloads").text

    assert "0 of 2 finished" in body
    # The one word the running transfer's own readout uses for an estimate.
    assert "Remaining" not in body


# --- the whole history at once -------------------------------------------------


def stop_all(test_client: TestClient, *urls: str) -> list[str]:
    """Queue each URL and take it straight back out, so the history has rows."""
    identifiers = [submit(test_client, url) for url in urls]
    for download_id in identifiers:
        test_client.post(f"/downloads/{download_id}/stop?back=/downloads")
    return identifiers


def test_two_things_left_to_try_are_offered_in_one_button(tmp_path: Path) -> None:
    with paused(tmp_path) as test_client:
        stop_all(test_client, FIRST, SECOND)

        body = test_client.get("/downloads").text

    assert "Try all 2 again" in body
    assert "/downloads/retry?back=/downloads" in body


def test_one_thing_left_to_try_is_left_to_its_own_row(tmp_path: Path) -> None:
    with paused(tmp_path) as test_client:
        stop_all(test_client, FIRST)

        body = test_client.get("/downloads").text

    assert "Try all" not in body
    assert "Try again" in body


def test_trying_everything_again_puts_it_all_back_in_order(tmp_path: Path) -> None:
    with paused(tmp_path) as test_client:
        stop_all(test_client, FIRST, SECOND)

        response = test_client.post("/downloads/retry?back=/downloads")

        assert response.url.path == "/downloads"
        assert waiting_labels(response.text) == [
            "https://mega.nz/file/AaBbCcDd",
            "https://mega.nz/file/EeFfGgHh",
        ]


def test_trying_everything_again_with_nothing_to_try_says_so(tmp_path: Path) -> None:
    """Only reachable from a page that has since gone stale, and it answers."""
    with paused(tmp_path) as test_client:
        response = test_client.post("/downloads/retry?back=/downloads")

    assert response.status_code == 409
    assert "ended without the file arriving" in response.text


def test_clearing_the_list_empties_the_history_and_its_counters(tmp_path: Path) -> None:
    with paused(tmp_path) as test_client:
        stop_all(test_client, FIRST, SECOND)

        response = test_client.post("/downloads/clear?back=/downloads")

    assert response.url.path == "/downloads"
    assert "Finished" not in response.text
    assert "Stopped" not in response.text


def test_clearing_the_list_leaves_what_is_waiting_where_it_was(tmp_path: Path) -> None:
    """The list got long; the work did not change."""
    with paused(tmp_path) as test_client:
        stop_all(test_client, FIRST)
        submit(test_client, SECOND)

        response = test_client.post("/downloads/clear?back=/downloads")

    assert waiting_labels(response.text) == ["https://mega.nz/file/EeFfGgHh"]
    assert "Try again" not in response.text


# --- the ends of the line ------------------------------------------------------


def test_the_first_row_cannot_be_moved_up_and_the_last_cannot_be_moved_down(
    tmp_path: Path,
) -> None:
    """A button that would do nothing is worse than one that is not there."""
    with paused(tmp_path) as test_client:
        first = submit(test_client, FIRST)
        last = submit(test_client, SECOND)

        body = test_client.get("/downloads").text

    assert f'/downloads/{first}/move?back=/downloads"' in body
    assert count_moves(body, first) == ["down"]
    assert count_moves(body, last) == ["top", "up"]


def count_moves(body: str, download_id: str) -> list[str]:
    """Return the moves offered for one row, in the order they are rendered."""
    rows = body.split(f"/downloads/{download_id}/move")
    return [
        match.group(1)
        for part in rows[1:]
        if (match := re.search(r'name="where" value="(\w+)"', part))
    ]


def test_a_lone_waiting_request_is_offered_no_move_at_all(tmp_path: Path) -> None:
    with paused(tmp_path) as test_client:
        only = submit(test_client, FIRST)

        body = test_client.get("/downloads").text

    assert count_moves(body, only) == []
    assert f"/downloads/{only}/stop" in body


# --- what the buttons do -------------------------------------------------------


def test_moving_a_request_up_reorders_the_table(tmp_path: Path) -> None:
    with paused(tmp_path) as test_client:
        submit(test_client, FIRST)
        second = submit(test_client, SECOND)

        response = test_client.post(
            f"/downloads/{second}/move?back=/downloads", data={"where": "up"}
        )

        assert response.status_code == 200
        assert waiting_labels(response.text)[0] == "https://mega.nz/file/EeFfGgHh"


def test_moving_a_request_to_the_front_puts_it_there(tmp_path: Path) -> None:
    with paused(tmp_path) as test_client:
        submit(test_client, FIRST)
        submit(test_client, SECOND)
        third = submit(test_client, THIRD)

        response = test_client.post(
            f"/downloads/{third}/move?back=/downloads", data={"where": "top"}
        )

        assert waiting_labels(response.text)[0] == "https://mega.nz/file/IiJjKkLl"


def test_a_move_nobody_named_changes_nothing(tmp_path: Path) -> None:
    """A form field is not a promise; anything but a move we know is ignored."""
    with paused(tmp_path) as test_client:
        submit(test_client, FIRST)
        second = submit(test_client, SECOND)

        response = test_client.post(
            f"/downloads/{second}/move?back=/downloads", data={"where": "sideways"}
        )

        assert response.status_code == 200
        assert waiting_labels(response.text)[0] == "https://mega.nz/file/AaBbCcDd"


def test_removing_a_waiting_request_takes_it_off_and_stays_on_the_queue(
    tmp_path: Path,
) -> None:
    with paused(tmp_path) as test_client:
        first = submit(test_client, FIRST)
        submit(test_client, SECOND)

        response = test_client.post(f"/downloads/{first}/stop?back=/downloads")

        assert response.status_code == 200
        assert response.url.path == "/downloads"
        assert waiting_labels(response.text) == ["https://mega.nz/file/EeFfGgHh"]


def test_removing_from_a_downloads_own_page_stays_on_that_page(tmp_path: Path) -> None:
    """The same button, and where it goes back to is the page it was pressed on."""
    with paused(tmp_path) as test_client:
        only = submit(test_client, FIRST)

        response = test_client.post(f"/downloads/{only}/stop", follow_redirects=False)

        assert response.headers["location"] == f"/downloads/{only}"


def test_pausing_and_resuming_come_back_to_the_queue(tmp_path: Path) -> None:
    with client(tmp_path) as test_client:
        held = test_client.post("/downloads/pause", data={"paused": "1"})
        assert ">Resume<" in held.text

        let_go = test_client.post("/downloads/pause", data={"paused": "0"})

        assert let_go.url.path == "/downloads"
        assert ">Pause<" in let_go.text


def test_resuming_lets_a_waiting_request_run(tmp_path: Path) -> None:
    with paused(tmp_path) as test_client:
        submit(test_client, FIRST)

        test_client.post("/downloads/pause", data={"paused": "0"})
        body = wait_until_quiet(test_client)

    assert waiting_labels(body) == []
    assert "Show the file" in body


def wait_until_quiet(test_client: TestClient, *, timeout: float = 10.0) -> str:
    """Return the queue page once nothing is left to do."""
    from time import monotonic, sleep

    deadline = monotonic() + timeout
    while monotonic() < deadline:
        body = test_client.get("/downloads").text
        if not queue_of(test_client).snapshot().is_busy:
            return body
        sleep(0.01)
    raise AssertionError(f"the queue did not drain within {timeout}s")


# --- the history ---------------------------------------------------------------


def test_a_removed_request_is_listed_as_stopped_rather_than_failed(tmp_path: Path) -> None:
    with paused(tmp_path) as test_client:
        only = submit(test_client, FIRST)

        body = test_client.post(f"/downloads/{only}/stop?back=/downloads").text

    assert "removed from the queue" in body
    assert "Stopped" in body
    assert "Failed" not in body


def test_a_finished_download_offers_the_file_rather_than_a_retry(tmp_path: Path) -> None:
    with client(tmp_path, provider=make_provider()) as test_client:
        submit(test_client, FIRST)

        body = wait_until_quiet(test_client)

    assert "Show the file" in body
    assert "Try again" not in body


def test_something_that_did_not_arrive_offers_a_retry(tmp_path: Path) -> None:
    with paused(tmp_path) as test_client:
        only = submit(test_client, FIRST)
        test_client.post(f"/downloads/{only}/stop?back=/downloads")

        body = test_client.get("/downloads").text

    assert "Try again" in body
    assert f"/downloads/{only}/retry?back=/downloads" in body


def test_retrying_from_the_queue_comes_back_to_the_queue(tmp_path: Path) -> None:
    with paused(tmp_path) as test_client:
        only = submit(test_client, FIRST)
        test_client.post(f"/downloads/{only}/stop?back=/downloads")

        response = test_client.post(f"/downloads/{only}/retry?back=/downloads")

        assert response.url.path == "/downloads"
        assert waiting_labels(response.text) == ["https://mega.nz/file/AaBbCcDd"]


def test_the_history_says_where_the_record_actually_lives(tmp_path: Path) -> None:
    """This list ends with the process; the library does not."""
    with paused(tmp_path) as test_client:
        only = submit(test_client, FIRST)

        body = test_client.post(f"/downloads/{only}/stop?back=/downloads").text

    assert "does not" in body
    assert 'href="/library"' in body


# --- the key -------------------------------------------------------------------


def test_the_queue_page_never_shows_a_share_key(tmp_path: Path) -> None:
    with paused(tmp_path) as test_client:
        submit(test_client, FIRST)
        removed = submit(test_client, SECOND)
        test_client.post(f"/downloads/{removed}/stop?back=/downloads")

        body = test_client.get("/downloads").text

    assert "https://mega.nz/file/AaBbCcDd" in body
    assert MEGA_KEY not in body


def test_something_that_never_ran_reports_no_duration(tmp_path: Path) -> None:
    """A removed request took no time because it never happened."""
    with paused(tmp_path) as test_client:
        only = submit(test_client, FIRST)

        body = test_client.post(f"/downloads/{only}/stop?back=/downloads").text

    assert "0.0 s" not in body
