"""Tests for queueing a set of links rather than one at a time.

Two controls with two different shapes, and the difference is the point:

*Queue selected* sends the ticked URLs in the body, because a share link keeps
its decryption key in the fragment and a fragment is what a browser drops from
a link. *Queue every match* sends only the filter, and the server resolves it
against what the crawl recorded — so no URL, and no key, makes the round trip.

The queue is paused throughout. These tests are about what gets queued, and a
worker draining it would reach the real mega.nz.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from test_api_pages import MEGA_LINK, findable_site, recording_client, start, wait_until_finished
from web_server import Site, serve

from maxicrawler.api.downloads import TransferQueue

KEY = MEGA_LINK.split("#", 1)[1]
SECOND_LINK = f"https://mega.nz/file/EeFfGgHh#{KEY}"


@contextmanager
def report(
    tmp_path: Path, site: Site | None = None, **settings: object
) -> Iterator[tuple[TestClient, str, str]]:
    """Yield a client, a finished crawl's id, and its rendered report.

    *settings* reach the application's configuration. What they are here for is
    ``direct_downloads=False``: with the shipped default every HTTP link can be
    fetched, so a report where only *some* rows are fetchable -- which is what
    several of these controls exist to handle -- is now the inspection-only
    installation rather than the ordinary one.
    """
    with (
        recording_client(tmp_path, **settings) as test_client,
        serve(site or findable_site()) as base,
    ):
        queue_of(test_client).pause()
        job_id = start(test_client, base)
        yield test_client, job_id, wait_until_finished(test_client, job_id)


def only_mega(test_client: TestClient, job_id: str) -> str:
    """Return the same report narrowed to the one link Mega claims.

    Several tests below want a filter that matches exactly one thing. Before
    the direct provider that was any unfiltered report; now it has to be asked
    for, and asking through the interface is better than asking through a
    configuration nobody runs.
    """
    return test_client.get(f"/crawls/{job_id}?plugin=mega").text


def queue_of(test_client: TestClient) -> TransferQueue:
    """Return the queue the application under test is using."""
    queue: TransferQueue = test_client.app.state.downloads  # type: ignore[attr-defined]
    return queue


def waiting_urls(test_client: TestClient) -> list[str]:
    """Return what is in the queue, as the runs hold it — without the keys."""
    return [item.url for item in queue_of(test_client).snapshot().waiting]


def fill_up(test_client: TestClient) -> None:
    """Leave the application with a queue that has no room in it.

    A fresh queue with a ceiling of one, already holding one, swapped in where
    the application looks for it. The alternative — submitting five hundred
    links — would test patience.
    """
    was = queue_of(test_client)
    full = TransferQueue(was.service, limit=1)
    full.pause()
    full.submit(MEGA_LINK)
    test_client.app.state.downloads = full  # type: ignore[attr-defined]
    was.shutdown(wait=False)


def ticked(body: str) -> list[str]:
    """Return the URLs the table offers a checkbox for."""
    return re.findall(
        r'<input type="checkbox" form="link-selection" name="url" value="([^"]+)"', body
    )


def matches_action(body: str) -> str:
    """Return where the "queue every match" button posts."""
    found = re.search(r'<form class="batch" method="post" action="([^"]+)"', body)
    assert found is not None, "the report offers no way to queue every match"
    return found.group(1)


# --- what the table offers -----------------------------------------------------


def test_a_link_that_can_be_fetched_gets_a_checkbox(tmp_path: Path) -> None:
    with report(tmp_path) as (_, _, body):
        assert MEGA_LINK in ticked(body)


def test_every_link_gets_one_now_that_ordinary_urls_can_be_fetched(tmp_path: Path) -> None:
    """The consequence of a provider that claims all of them, stated plainly."""
    with report(tmp_path) as (_, _, body):
        assert len(ticked(body)) == 3


def test_a_link_that_cannot_be_fetched_gets_none(tmp_path: Path) -> None:
    """A column of empty boxes would invite a click that does nothing.

    Only an inspection-only installation still has such a link, so that is
    where the rule is exercised -- the rule itself has not changed.
    """
    with report(tmp_path, direct_downloads=False) as (_, _, body):
        assert ticked(body) == [MEGA_LINK]
        assert body.count('type="checkbox" form="link-selection"') == 1


def test_a_report_with_nothing_fetchable_offers_no_batch_at_all(tmp_path: Path) -> None:
    site = Site()
    site.add_html("/", '<a href="/a">a</a>')
    site.add_html("/a", "<p>x</p>")

    with report(tmp_path, site, direct_downloads=False) as (_, _, body):
        assert 'id="link-selection"' not in body
        assert "Queue every fetchable match" not in body


def test_the_header_offers_to_tick_every_row_and_starts_hidden(tmp_path: Path) -> None:
    """A checkbox that ticks nothing is worse than no checkbox (ADR-023)."""
    with report(tmp_path) as (_, _, body):
        found = re.search(r'<input type="checkbox" class="tick-all"[^>]*>', body)

        assert found is not None
        assert "hidden" in found.group(0)
        assert 'aria-label="Select every link on this page"' in found.group(0)


def test_the_header_checkbox_is_a_control_and_never_a_field(tmp_path: Path) -> None:
    """It has no name, so whatever it does to the rows is all that is submitted."""
    with report(tmp_path) as (_, _, body):
        found = re.search(r'<input type="checkbox" class="tick-all"[^>]*>', body)

        assert found is not None
        assert "name=" not in found.group(0)
        assert "form=" not in found.group(0)


def test_the_counter_starts_hidden_and_at_nothing(tmp_path: Path) -> None:
    with report(tmp_path) as (_, _, body):
        assert '<span class="chosen muted small" hidden><span class="num">0</span> selected' in body


def test_the_script_is_asked_for_only_where_there_are_boxes(tmp_path: Path) -> None:
    site = Site()
    site.add_html("/", '<a href="/a">a</a>')
    site.add_html("/a", "<p>x</p>")

    with report(tmp_path, site, direct_downloads=False) as (_, _, body):
        assert "/static/select.js" not in body
        assert 'class="tick-all"' not in body


def test_the_script_is_asked_for_where_there_are(tmp_path: Path) -> None:
    with report(tmp_path) as (_, _, body):
        assert '<script src="/static/select.js" defer></script>' in body


def test_the_selection_script_is_served(tmp_path: Path) -> None:
    with report(tmp_path) as (test_client, _, _):
        response = test_client.get("/static/select.js")

        assert response.status_code == 200
        # It finds its boxes by the marker, not by this page's form id: the
        # library has a selection of its own now and shares the script.
        assert "data-tick" in response.text


def test_the_checkboxes_belong_to_a_form_they_are_not_inside(tmp_path: Path) -> None:
    """Forms cannot nest, and every downloadable row already has one."""
    with report(tmp_path) as (_, _, body):
        assert '<form class="batch" id="link-selection"' in body
        # The row's own button is still there, and still its own form.
        assert '<form class="row-action" method="post" action="/downloads">' in body


# --- queueing what was ticked --------------------------------------------------


def test_the_ticked_links_are_queued(tmp_path: Path) -> None:
    with report(tmp_path) as (test_client, _, _):
        response = test_client.post(
            "/downloads/selection", data={"url": [MEGA_LINK, SECOND_LINK]}, follow_redirects=False
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/downloads"
        assert waiting_urls(test_client) == [
            "https://mega.nz/file/AaBbCcDd",
            "https://mega.nz/file/EeFfGgHh",
        ]


def test_one_ticked_link_goes_to_its_own_page(tmp_path: Path) -> None:
    """Somebody who queued one download wants to watch that download."""
    with report(tmp_path) as (test_client, _, _):
        response = test_client.post(
            "/downloads/selection", data={"url": [MEGA_LINK]}, follow_redirects=False
        )

        assert response.headers["location"].startswith("/downloads/")
        assert response.headers["location"] != "/downloads"


def test_a_bad_link_in_the_batch_does_not_refuse_the_rest(tmp_path: Path) -> None:
    with report(tmp_path) as (test_client, _, _):
        response = test_client.post(
            "/downloads/selection",
            data={"url": [MEGA_LINK, "not-a-url", SECOND_LINK]},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert len(waiting_urls(test_client)) == 2


def test_a_batch_of_nothing_but_rubbish_is_refused_with_a_count(tmp_path: Path) -> None:
    with report(tmp_path) as (test_client, _, _):
        response = test_client.post(
            "/downloads/selection", data={"url": ["nonsense", "also nonsense"]}
        )

        assert response.status_code == 409
        assert "none of the 2 links selected" in response.text


def test_ticking_nothing_is_not_an_error(tmp_path: Path) -> None:
    """An empty selection is a click somebody will simply repeat.

    Posted as a browser posts a form with nothing ticked: the content type is
    still there, and the body is empty. Passing no data at all would test
    ``httpx`` rather than the handler.
    """
    with report(tmp_path) as (test_client, _, _):
        response = test_client.post(
            "/downloads/selection",
            content=b"",
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/downloads"
        assert waiting_urls(test_client) == []


def test_a_full_queue_refuses_a_selection_and_says_why(tmp_path: Path) -> None:
    with report(tmp_path) as (test_client, _, _):
        fill_up(test_client)

        response = test_client.post("/downloads/selection", data={"url": [SECOND_LINK]})

        assert response.status_code == 409
        assert "no room" in response.text


# --- queueing everything a filter matches --------------------------------------


def test_every_fetchable_match_is_queued_without_any_url_being_sent(tmp_path: Path) -> None:
    """The whole point: the browser sends a filter, not a list of links.

    Narrowed to one plugin so the queue is readable. That the filter is what
    travelled is exactly what makes the narrowing possible from here.
    """
    with report(tmp_path) as (test_client, job_id, body):
        filtered = only_mega(test_client, job_id)

        response = test_client.post(matches_action(filtered), follow_redirects=False)

        assert response.status_code == 303
        assert waiting_urls(test_client) == ["https://mega.nz/file/AaBbCcDd"]


def test_the_action_carries_the_filter_that_was_on_screen(tmp_path: Path) -> None:
    with report(tmp_path) as (test_client, job_id, _):
        filtered = test_client.get(f"/crawls/{job_id}?plugin=mega").text

        assert matches_action(filtered) == f"/crawls/{job_id}/downloads?plugin=mega"


def test_a_filter_matching_nothing_fetchable_says_so_rather_than_queueing_nothing(
    tmp_path: Path,
) -> None:
    with report(tmp_path) as (test_client, job_id, _):
        response = test_client.post(f"/crawls/{job_id}/downloads?q=nothing-matches-this")

        assert response.status_code == 409
        assert "nothing this filter matches can be downloaded" in response.text


def test_a_filter_asking_for_what_cannot_be_fetched_queues_nothing(tmp_path: Path) -> None:
    with report(tmp_path) as (test_client, job_id, _):
        response = test_client.post(f"/crawls/{job_id}/downloads?dl=no")

        assert response.status_code == 409
        assert waiting_urls(test_client) == []


def test_a_full_queue_refuses_the_whole_filter_and_names_the_number(tmp_path: Path) -> None:
    with report(tmp_path) as (test_client, job_id, _):
        fill_up(test_client)

        response = test_client.post(f"/crawls/{job_id}/downloads")

        assert response.status_code == 409
        assert "3 links match, and the queue has no room" in response.text


def test_a_crawl_nobody_recorded_matches_nothing(tmp_path: Path) -> None:
    with report(tmp_path) as (test_client, _, _):
        response = test_client.post("/crawls/no-such-crawl/downloads")

        assert response.status_code == 409


# --- queueing only what is not known yet ---------------------------------------


def test_queueing_every_new_match_leaves_out_what_is_already_queued(tmp_path: Path) -> None:
    """The re-crawl in two clicks: pick "new", then queue every match of it.

    The state the filter names is resolved on the server against what the crawl
    recorded, exactly as the plugin filter is. Nothing about which links were
    already known has to travel to a browser and back to be left out.
    """
    with report(tmp_path) as (test_client, job_id, _):
        test_client.post("/downloads", data={"url": MEGA_LINK}, follow_redirects=False)
        body = test_client.get(f"/crawls/{job_id}?state=%28new%29").text

        test_client.post(matches_action(body))

        assert waiting_urls(test_client).count("https://mega.nz/file/AaBbCcDd") == 1
        assert len(waiting_urls(test_client)) == 3


def test_the_action_carries_the_state_that_was_on_screen(tmp_path: Path) -> None:
    with report(tmp_path) as (test_client, job_id, _):
        filtered = test_client.get(f"/crawls/{job_id}?state=%28new%29").text

        assert matches_action(filtered) == f"/crawls/{job_id}/downloads?state=%28new%29"


# --- the way back --------------------------------------------------------------


def narrow_to(test_client: TestClient, limit: int) -> None:
    """Leave the application with a queue that has room for exactly *limit*."""
    was = queue_of(test_client)
    smaller = TransferQueue(was.service, limit=limit)
    smaller.pause()
    test_client.app.state.downloads = smaller  # type: ignore[attr-defined]
    was.shutdown(wait=False)


def test_a_selection_lands_back_at_the_table_it_was_ticked_on(tmp_path: Path) -> None:
    """The click this sprint exists to remove: the trip to the queue and back."""
    with report(tmp_path) as (test_client, job_id, _):
        response = test_client.post(
            "/downloads/selection",
            data={"url": [MEGA_LINK], "back": f"/crawls/{job_id}?plugin=mega#links"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        location = response.headers["location"]
        assert location.startswith(f"/crawls/{job_id}?plugin=mega")
        assert location.endswith("#links")


def test_the_form_offers_the_filter_that_is_on_screen_as_the_way_back(tmp_path: Path) -> None:
    with report(tmp_path) as (test_client, job_id, _):
        body = test_client.get(f"/crawls/{job_id}?plugin=mega").text

        assert f'name="back" value="/crawls/{job_id}?plugin=mega#links"' in body


def test_queueing_every_match_lands_back_at_the_same_filter(tmp_path: Path) -> None:
    """Rebuilt from the filter that was queued, so the two cannot disagree."""
    with report(tmp_path) as (test_client, job_id, _):
        response = test_client.post(
            matches_action(only_mega(test_client, job_id)), follow_redirects=False
        )

        location = response.headers["location"]
        assert location.startswith(f"/crawls/{job_id}?plugin=mega")
        assert location.endswith("#links")


def test_the_report_says_how_many_it_queued(tmp_path: Path) -> None:
    with report(tmp_path) as (test_client, job_id, _):
        response = test_client.post(
            "/downloads/selection",
            data={"url": [MEGA_LINK, SECOND_LINK], "back": f"/crawls/{job_id}#links"},
            follow_redirects=False,
        )
        body = test_client.get(response.headers["location"]).text

        assert "2 links queued." in body
        assert 'href="/downloads">Watch the queue' in body


def test_one_queued_link_is_not_called_links(tmp_path: Path) -> None:
    with report(tmp_path) as (test_client, job_id, _):
        body = test_client.get(f"/crawls/{job_id}?queued=1").text

        assert "1 link queued." in body


def test_the_report_names_what_did_not_fit(tmp_path: Path) -> None:
    """A job mostly done is not a job done, and the difference is the remainder."""
    with report(tmp_path) as (test_client, job_id, _):
        narrow_to(test_client, 2)

        response = test_client.post(f"/crawls/{job_id}/downloads", follow_redirects=False)
        body = test_client.get(response.headers["location"]).text

        assert "2 links queued." in body
        assert "1 did not fit — the queue is full." in body


def test_the_confirmation_is_gone_the_moment_anything_else_is_clicked(tmp_path: Path) -> None:
    """It is about the click that just happened, not about the report."""
    with report(tmp_path) as (test_client, job_id, _):
        body = test_client.get(f"/crawls/{job_id}?queued=2").text

        assert "2 links queued." in body
        assert "queued=" not in body.split("Discovered links", 1)[1]


def test_the_confirmation_survives_neither_a_filter_nor_the_other_table(tmp_path: Path) -> None:
    with report(tmp_path) as (test_client, job_id, _):
        body = test_client.get(f"/crawls/{job_id}?queued=2&pstate=visited").text

        assert "queued=" not in body


def test_a_way_back_to_another_host_is_refused(tmp_path: Path) -> None:
    """A browser reads "//elsewhere.test/" as another host, so a path is checked."""
    with report(tmp_path) as (test_client, _, _):
        response = test_client.post(
            "/downloads/selection",
            data={"url": [MEGA_LINK], "back": "//elsewhere.test/"},
            follow_redirects=False,
        )

        assert response.headers["location"].startswith("/downloads")


def test_a_batch_that_named_no_way_back_still_goes_to_the_queue(tmp_path: Path) -> None:
    """Nothing in the report posts without one; what is left is everything else."""
    with report(tmp_path) as (test_client, _, _):
        response = test_client.post(
            "/downloads/selection",
            data={"url": [MEGA_LINK, SECOND_LINK]},
            follow_redirects=False,
        )

        assert response.headers["location"] == "/downloads"


def test_a_nonsense_count_renders_the_report_rather_than_refusing_it(tmp_path: Path) -> None:
    with report(tmp_path) as (test_client, job_id, _):
        response = test_client.get(f"/crawls/{job_id}?queued=lots&full=-3")

        assert response.status_code == 200
        assert "Nothing was queued." in response.text
        assert "did not fit" not in response.text


# --- the key -------------------------------------------------------------------


def test_the_filter_action_carries_no_key(tmp_path: Path) -> None:
    """It is a URL a browser puts in its history and a proxy writes down."""
    with report(tmp_path) as (_, _, body):
        assert KEY not in matches_action(body)


def test_the_way_back_carries_no_key_either(tmp_path: Path) -> None:
    """It is built from the filter, which holds no URL and so can hold no key."""
    with report(tmp_path) as (_, _, body):
        found = re.search(r'name="back" value="([^"]+)"', body)

        assert found is not None
        assert KEY not in found.group(1)


def test_the_redirect_back_to_the_report_carries_no_key(tmp_path: Path) -> None:
    with report(tmp_path) as (test_client, job_id, _):
        response = test_client.post(
            "/downloads/selection",
            data={"url": [MEGA_LINK], "back": f"/crawls/{job_id}#links"},
            follow_redirects=False,
        )

        assert KEY not in response.headers["location"]


def test_queueing_a_whole_filter_still_gets_the_key_to_the_transfer(tmp_path: Path) -> None:
    """Resolved on the server, from what discovery wrote down, fragment and all."""
    with report(tmp_path) as (test_client, job_id, body):
        test_client.post(matches_action(only_mega(test_client, job_id)))

        queue = queue_of(test_client)
        (waiting,) = queue.snapshot().waiting
        # Held where it is always held, and visible in no snapshot.
        assert KEY not in repr(queue.snapshot())
        assert queue._targets[waiting.download_id] == MEGA_LINK  # noqa: SLF001


def test_the_redirect_after_a_batch_carries_no_key(tmp_path: Path) -> None:
    with report(tmp_path) as (test_client, _, _):
        response = test_client.post(
            "/downloads/selection", data={"url": [MEGA_LINK]}, follow_redirects=False
        )

        assert KEY not in response.headers["location"]


def test_the_queue_page_after_a_batch_shows_no_key(tmp_path: Path) -> None:
    with report(tmp_path) as (test_client, _, body):
        test_client.post(matches_action(body))

        assert KEY not in test_client.get("/downloads").text


@pytest.mark.parametrize("action", ["/downloads/selection", "/downloads"])
def test_a_body_that_is_not_a_form_is_refused(tmp_path: Path, action: str) -> None:
    """Saying so beats quietly seeing no fields at all."""
    with report(tmp_path) as (test_client, _, _):
        response = test_client.post(action, json={"url": MEGA_LINK})

        assert response.status_code == 415


def test_the_spoken_label_of_a_checkbox_leaves_the_key_out(tmp_path: Path) -> None:
    """A screen reader announcing forty random characters helps nobody."""
    with report(tmp_path) as (_, _, body):
        found = re.search(r'aria-label="Queue ([^"]+)"', body)

        assert found is not None
        assert found.group(1) == "https://mega.nz/file/AaBbCcDd"


def test_the_count_beside_the_button_is_about_the_filter_not_the_button(tmp_path: Path) -> None:
    """Three matches where one is fetchable must not read as a promise of three.

    The two numbers can only differ where some links are not fetchable, which
    since the direct provider means an inspection-only installation. The rule
    is unchanged and this is the one place left that can still show it.
    """
    with report(tmp_path, direct_downloads=False) as (_, _, body):
        assert ticked(body) == [MEGA_LINK]  # one link is fetchable
        assert "of the 3 links this filter matches" in body
