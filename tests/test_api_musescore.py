"""The page that joins today's list to what the browser downloaded.

The property worth guarding hardest is the last one here. This page takes a
*file path* from a form, on an interface with no sign-in, and copies what it
finds into the library. Without a rule confining that to the download folder it
would be a way for anybody who can reach the port to read any file on the
machine. That rule lives in the service; this checks the page cannot get round
it.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from maxicrawler.api import create_app
from maxicrawler.app import CrawlService
from maxicrawler.config import Settings

SCORE = "https://musescore.com/user/21965011/scores/4217351"
PDF = b"%PDF-1.4 a score with enough bytes to be kept\n"


@contextmanager
def client(tmp_path: Path, **overrides: object) -> Iterator[tuple[TestClient, Path]]:
    """Yield a client and the download folder its worklist reads."""
    downloads = tmp_path / "Downloads"
    downloads.mkdir(exist_ok=True)
    settings = Settings(
        database_path=tmp_path / "urls.db",
        library_path=tmp_path / "library",
        musescore_downloads=str(downloads),
        min_download_size=0,
        **overrides,  # type: ignore[arg-type]
    )
    application = create_app(service=CrawlService(settings), settings=settings)
    with TestClient(application) as test_client:
        yield test_client, downloads


def add_score(test_client: TestClient, url: str = SCORE) -> None:
    """Put every rendering of *url* on the backlog."""
    test_client.post("/musescore", data={"urls": url})


def store_ids(page: str) -> list[str]:
    """Return the request ids the page offers a Keep button for."""
    return re.findall(r"/musescore/([0-9a-f]+)/store", page)


def test_the_page_is_reachable_and_says_what_the_allowance_is(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, _):
        response = test_client.get("/musescore")

    assert response.status_code == 200
    assert ">0</b> of 20 taken" in response.text


def test_the_page_says_it_cannot_fetch_from_this_host(tmp_path: Path) -> None:
    """The one thing a reader must not have to work out for themselves."""
    with client(tmp_path) as (test_client, _):
        response = test_client.get("/musescore")

    assert "cannot fetch" in response.text
    assert "bot check" in response.text


def test_pasted_addresses_become_one_line_per_rendering(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, _):
        add_score(test_client)
        page = test_client.get("/musescore").text

    assert page.count("/drop") == 2


def test_a_paste_holding_no_score_says_so_rather_than_silently_doing_nothing(
    tmp_path: Path,
) -> None:
    with client(tmp_path) as (test_client, _):
        response = test_client.post("/musescore", data={"urls": "https://example.org/"})

    assert response.status_code == 400
    assert "score address" in response.text


def test_an_empty_paste_is_not_an_error(tmp_path: Path) -> None:
    """Submitting an empty box is a slip, not a complaint worth making."""
    with client(tmp_path) as (test_client, _):
        response = test_client.post("/musescore", data={"urls": "  "})

    assert response.status_code == 200


def test_a_file_in_the_download_folder_is_offered_against_a_line(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, downloads):
        add_score(test_client)
        (downloads / "Hallelujah.pdf").write_bytes(PDF)
        page = test_client.get("/musescore").text

    assert "Hallelujah.pdf" in page
    assert "Keep it" in page


def test_keeping_a_file_spends_a_day_and_leaves_the_file_alone(tmp_path: Path) -> None:
    """The library gains a copy; the Downloads folder is untouched."""
    with client(tmp_path) as (test_client, downloads):
        add_score(test_client)
        arrived = downloads / "Hallelujah.pdf"
        arrived.write_bytes(PDF)
        request_id = store_ids(test_client.get("/musescore").text)[0]

        response = test_client.post(
            f"/musescore/{request_id}/store", data={"path": arrived.as_posix()}
        )
        page = test_client.get("/musescore").text

    assert response.status_code == 200
    assert ">1</b> of 20 taken" in page
    assert arrived.exists()
    assert arrived.read_bytes() == PDF


def test_an_ambiguous_arrival_is_explained_rather_than_guessed(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, downloads):
        add_score(test_client)
        add_score(test_client, "https://musescore.com/user/1/scores/999")
        (downloads / "one.pdf").write_bytes(PDF)
        (downloads / "two.pdf").write_bytes(PDF)
        page = test_client.get("/musescore").text

    assert "could be this pdf" in page
    assert "Keep it" not in page


def test_dropping_a_line_takes_it_off_without_spending_anything(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, _):
        add_score(test_client)
        request_id = re.findall(r"/musescore/([0-9a-f]+)/drop", test_client.get("/musescore").text)[
            0
        ]

        test_client.post(f"/musescore/{request_id}/drop", data={"note": "wrong arrangement"})
        page = test_client.get("/musescore").text

    assert ">0</b> of 20 taken" in page
    assert page.count("/drop") == 1


def test_a_spent_allowance_offers_nothing_more(tmp_path: Path) -> None:
    with client(tmp_path, musescore_daily_limit=1, musescore_formats=("pdf",)) as (
        test_client,
        downloads,
    ):
        add_score(test_client)
        arrived = downloads / "Hallelujah.pdf"
        arrived.write_bytes(PDF)
        request_id = store_ids(test_client.get("/musescore").text)[0]
        test_client.post(f"/musescore/{request_id}/store", data={"path": arrived.as_posix()})

        page = test_client.get("/musescore").text

    assert "allowance is spent" in page


def test_a_file_outside_the_download_folder_is_refused(tmp_path: Path) -> None:
    """The reason this page is allowed to take a path at all.

    The interface has no sign-in (ADR-025). A path it accepted without this
    check would let anybody who can reach the port copy any readable file on
    the machine into the library.
    """
    private = tmp_path / "private.pem"
    private.write_bytes(b"a key nobody should be able to ask for over HTTP\n")
    with client(tmp_path) as (test_client, _):
        add_score(test_client)
        request_id = re.findall(r"/musescore/([0-9a-f]+)/drop", test_client.get("/musescore").text)[
            0
        ]

        response = test_client.post(
            f"/musescore/{request_id}/store", data={"path": private.as_posix()}
        )

    assert response.status_code == 400
    assert "download folder" in response.text


def test_a_path_climbing_out_of_the_folder_is_refused(tmp_path: Path) -> None:
    """Judged by where it lands, not by how it was spelled."""
    private = tmp_path / "private.pem"
    private.write_bytes(b"a key nobody should be able to ask for over HTTP\n")
    with client(tmp_path) as (test_client, downloads):
        add_score(test_client)
        request_id = re.findall(r"/musescore/([0-9a-f]+)/drop", test_client.get("/musescore").text)[
            0
        ]

        response = test_client.post(
            f"/musescore/{request_id}/store",
            data={"path": (downloads / ".." / "private.pem").as_posix()},
        )

    assert response.status_code == 400


def test_the_worklist_survives_a_restart(tmp_path: Path) -> None:
    """The whole point of the feature, checked through the interface."""
    with client(tmp_path) as (test_client, _):
        add_score(test_client)

    with client(tmp_path) as (test_client, _):
        page = test_client.get("/musescore").text

    assert page.count("/drop") == 2


def test_musescore_is_in_the_navigation(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, _):
        page = test_client.get("/musescore").text

    assert ">MuseScore<" in page


@pytest.mark.parametrize("method", ["get", "delete", "put"])
def test_only_the_intended_methods_are_answered(tmp_path: Path, method: str) -> None:
    with client(tmp_path) as (test_client, _):
        response = getattr(test_client, method)("/musescore/anything/store")

    assert response.status_code == 405
