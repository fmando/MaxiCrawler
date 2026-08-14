"""Tests for saying what you think of a stored file, over HTTP.

Everything here goes through the routes rather than the service, because what
is under test is the arrangement around it: which form field means what, where
a judgement lands afterwards, and what a page refuses to obey.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from starlette.testclient import TestClient

from maxicrawler.api import create_app
from maxicrawler.app import CrawlService, LibraryService
from maxicrawler.config import Settings
from maxicrawler.domain import ResourceKind, ResourceRef, ReviewVerdict
from maxicrawler.library import Library

PAYLOAD = b"stub payload"


def store(library: Library, handle: str, *, name: str, provider: str = "mega") -> str:
    """Write one library entry by hand and return its key."""
    ref = ResourceRef(
        provider=provider,
        resource_id=handle,
        kind=ResourceKind.FILE,
        url=f"https://{provider}.nz/file/{handle}",
    )
    entry = library.entry(ref)
    entry.path.mkdir(parents=True, exist_ok=True)
    stored = entry.content_path(name)
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(PAYLOAD)
    entry.metadata_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "provider": provider,
                "key": entry.key,
                "resource_id": handle,
                "parent_id": None,
                "kind": "file",
                "name": name,
                "source_url": ref.url,
                "source_document": None,
                "status": "completed",
                "discovered_at": None,
                "downloaded_at": datetime(2026, 8, 9, 14, 30, tzinfo=UTC).isoformat(),
                "attempts": 1,
                "error": None,
                "content": {
                    "filename": name,
                    "path": f"content/{name}",
                    "size": len(PAYLOAD),
                    "checksums": [],
                },
            }
        ),
        encoding="utf-8",
    )
    return entry.key


@contextmanager
def client(tmp_path: Path) -> Iterator[tuple[TestClient, LibraryService, Library]]:
    """Yield a client over an application whose library is below *tmp_path*."""
    settings = Settings(
        database_path=tmp_path / "urls.db",
        library_path=tmp_path / "library",
        min_download_size=0,
    )
    library = Library(settings.library_path)
    service = LibraryService(settings, library=library)
    application = create_app(service=CrawlService(settings), library=service)
    with TestClient(application) as test_client:
        yield test_client, service, library


def mark_discarded(library: Library, provider: str, key: str) -> None:
    """Write a discarded verdict straight into the document.

    By hand, because the service refuses to write this one: the word means the
    payload has been removed, and removing it is a step of its own. Until that
    step exists, a test that needs a tombstone builds one.
    """
    entry = library.entry_at(provider, key)
    assert entry is not None
    document = json.loads(entry.metadata_path.read_text(encoding="utf-8"))
    document["review"] = {
        "verdict": "discarded",
        "favourite": False,
        "reviewed_at": None,
        "payload_removed_at": None,
    }
    entry.metadata_path.write_text(json.dumps(document), encoding="utf-8")


# --- one file at a time -------------------------------------------------------


def test_a_verdict_posted_for_one_file_is_recorded(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, service, library):
        key = store(library, "AaBbCcDd", name="Jump.pdf")

        response = test_client.post(
            f"/library/mega/{key}/review", data={"verdict": "kept"}, follow_redirects=False
        )

        assert response.status_code == 303
        item = service.item("mega", key)
        assert item is not None
        assert item.verdict is ReviewVerdict.KEPT


def test_judging_one_file_lands_back_on_its_own_page(tmp_path: Path) -> None:
    """Somebody standing on a file is looking at it, not finished with it."""
    with client(tmp_path) as (test_client, _, library):
        key = store(library, "AaBbCcDd", name="Jump.pdf")

        response = test_client.post(
            f"/library/mega/{key}/review",
            data={"verdict": "kept"},
            follow_redirects=False,
        )

        assert response.headers["location"] == f"/library/mega/{key}"


def test_the_form_says_where_to_land_and_is_obeyed(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, _, library):
        key = store(library, "AaBbCcDd", name="Jump.pdf")

        response = test_client.post(
            f"/library/mega/{key}/review?back=/library%3Fkind%3Dpdf",
            data={"verdict": "ignored"},
            follow_redirects=False,
        )

        assert response.headers["location"] == "/library?kind=pdf"


def test_somewhere_that_is_not_ours_is_not_obeyed(tmp_path: Path) -> None:
    """ADR-039: a leading `//` is another host, which is the open redirect."""
    with client(tmp_path) as (test_client, _, library):
        key = store(library, "AaBbCcDd", name="Jump.pdf")

        response = test_client.post(
            f"/library/mega/{key}/review?back=//elsewhere.test/",
            data={"verdict": "kept"},
            follow_redirects=False,
        )

        assert response.headers["location"] == f"/library/mega/{key}"


def test_the_star_is_a_switch_of_its_own(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, service, library):
        key = store(library, "AaBbCcDd", name="Jump.pdf")
        test_client.post(f"/library/mega/{key}/review", data={"verdict": "kept"})

        test_client.post(f"/library/mega/{key}/review", data={"favourite": "1"})
        item = service.item("mega", key)
        assert item is not None
        assert item.favourite is True
        assert item.verdict is ReviewVerdict.KEPT

        test_client.post(f"/library/mega/{key}/review", data={"favourite": "0"})
        item = service.item("mega", key)
        assert item is not None
        assert item.favourite is False
        assert item.verdict is ReviewVerdict.KEPT


def test_a_verdict_button_does_not_quietly_unstar_what_it_judges(tmp_path: Path) -> None:
    """An absent field means "this form was not about the star"."""
    with client(tmp_path) as (test_client, service, library):
        key = store(library, "AaBbCcDd", name="Jump.pdf")
        test_client.post(f"/library/mega/{key}/review", data={"favourite": "1"})

        test_client.post(f"/library/mega/{key}/review", data={"verdict": "ignored"})

        item = service.item("mega", key)
        assert item is not None
        assert item.favourite is True


def test_judging_a_file_that_is_not_there_is_a_404(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, _, _):
        response = test_client.post("/library/mega/nothing/review", data={"verdict": "kept"})

        assert response.status_code == 404


# --- a batch of them ----------------------------------------------------------


def test_the_ticked_files_are_judged_together(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, service, library):
        first = store(library, "AaBbCcDd", name="one.pdf")
        second = store(library, "EeFfGgHh", name="two.pdf")
        third = store(library, "IiJjKkLl", name="three.pdf")

        test_client.post(
            "/library/review",
            data={"verdict": "kept", "entry": [f"mega/{first}", f"mega/{second}"]},
        )

        assert service.item("mega", first).verdict is ReviewVerdict.KEPT  # type: ignore[union-attr]
        assert service.item("mega", second).verdict is ReviewVerdict.KEPT  # type: ignore[union-attr]
        assert service.item("mega", third).verdict is ReviewVerdict.UNREVIEWED  # type: ignore[union-attr]


def test_a_batch_lands_back_in_the_listing_it_came_from(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, _, library):
        key = store(library, "AaBbCcDd", name="one.pdf")

        response = test_client.post(
            "/library/review?back=/library%3Fverdict%3Dunreviewed",
            data={"verdict": "kept", "entry": [f"mega/{key}"]},
            follow_redirects=False,
        )

        assert response.headers["location"] == "/library?verdict=unreviewed"


def test_an_entry_that_has_gone_does_not_refuse_the_rest(tmp_path: Path) -> None:
    """Partial by design, the way queueing a selection is."""
    with client(tmp_path) as (test_client, service, library):
        key = store(library, "AaBbCcDd", name="one.pdf")

        response = test_client.post(
            "/library/review",
            data={"verdict": "kept", "entry": ["mega/gone", f"mega/{key}", "nonsense"]},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert service.item("mega", key).verdict is ReviewVerdict.KEPT  # type: ignore[union-attr]


def test_a_batch_saying_nothing_changes_nothing(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, service, library):
        key = store(library, "AaBbCcDd", name="one.pdf")

        test_client.post("/library/review", data={"entry": [f"mega/{key}"]})

        assert service.item("mega", key).verdict is ReviewVerdict.UNREVIEWED  # type: ignore[union-attr]


# --- what the pages carry -----------------------------------------------------


def test_every_tile_offers_the_same_four_controls(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, _, library):
        key = store(library, "AaBbCcDd", name="Jump.pdf")

        body = test_client.get("/library").text

        assert f'action="/library/mega/{key}/review?back=' in body
        assert 'name="verdict" value="kept"' in body
        assert 'name="verdict" value="ignored"' in body
        assert 'name="favourite"' in body
        # Discarding removes the payload and is not offered until it does.
        assert 'value="discarded"' not in body


def test_a_tile_carries_a_tick_that_belongs_to_the_batch(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, _, library):
        key = store(library, "AaBbCcDd", name="Jump.pdf")

        body = test_client.get("/library").text

        assert '<form class="batch" id="library-selection"' in body
        assert 'form="library-selection"' in body
        assert f'value="mega/{key}"' in body
        # What `select.js` finds the boxes by, here and on the report alike.
        assert "data-tick" in body


def test_undo_is_offered_only_once_there_is_something_to_undo(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, service, library):
        key = store(library, "AaBbCcDd", name="Jump.pdf")

        assert 'value="unreviewed"' not in test_client.get("/library").text

        service.review("mega", key, verdict=ReviewVerdict.KEPT)
        assert 'value="unreviewed"' in test_client.get("/library").text


def test_the_review_chips_lead_to_the_pile_worth_working_through(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, service, library):
        store(library, "AaBbCcDd", name="one.pdf")
        key = store(library, "EeFfGgHh", name="two.pdf")
        service.review("mega", key, verdict=ReviewVerdict.KEPT)

        body = test_client.get("/library").text

        assert "Review" in body
        assert "verdict=unreviewed" in body
        assert "verdict=kept" in body


def test_a_discarded_file_is_absent_until_its_chip_is_followed(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, service, library):
        key = store(library, "AaBbCcDd", name="gone.pdf")
        mark_discarded(library, "mega", key)

        assert "gone.pdf" not in test_client.get("/library").text
        assert "gone.pdf" in test_client.get("/library?verdict=discarded").text


def test_discarding_is_not_a_word_this_route_writes(tmp_path: Path) -> None:
    """A tombstone over a file that is still there would make it unreachable."""
    with client(tmp_path) as (test_client, service, library):
        key = store(library, "AaBbCcDd", name="Jump.pdf")

        response = test_client.post(f"/library/mega/{key}/review", data={"verdict": "discarded"})

        assert response.status_code == 400
        item = service.item("mega", key)
        assert item is not None
        assert item.verdict is ReviewVerdict.UNREVIEWED


def test_a_batch_cannot_discard_either(tmp_path: Path) -> None:
    with client(tmp_path) as (test_client, service, library):
        key = store(library, "AaBbCcDd", name="Jump.pdf")

        response = test_client.post(
            "/library/review", data={"verdict": "discarded", "entry": [f"mega/{key}"]}
        )

        assert response.status_code == 400
        assert service.item("mega", key).verdict is ReviewVerdict.UNREVIEWED  # type: ignore[union-attr]
