"""Tests for the page that says what to run, and never runs it.

Two things are under test. That the page carries what somebody needs — every
run, and a line that would work if pasted on this machine — and that it carries
nothing else: no form, no button that acts, and no route that would accept one.

The second is the more important of the two. It is the whole reason the page is
allowed to exist on an interface with no sign-in, and it is the kind of property
that is quietly lost to a later convenience.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from maxicrawler.api import create_app
from maxicrawler.app import CrawlService, LibraryService
from maxicrawler.app.maintenance import RUNS
from maxicrawler.app.thumbnails import SUFFIX, cache_beside
from maxicrawler.config import Settings
from maxicrawler.domain import ResourceKind, ResourceRef
from maxicrawler.library import Library
from maxicrawler.utils import format_size


@contextmanager
def client(tmp_path: Path, *, config: Path | None = None) -> Iterator[TestClient]:
    """Yield a client over an application whose library is below *tmp_path*."""
    settings = Settings(
        database_path=tmp_path / "urls.db",
        library_path=tmp_path / "library",
    )
    service = LibraryService(settings, library=Library(settings.library_path))
    application = create_app(service=CrawlService(settings), library=service, config_path=config)
    with TestClient(application) as test_client:
        yield test_client


def store_image(library: Library, handle: str) -> None:
    """Write one stored entry, so the library is not empty."""
    ref = ResourceRef(
        provider="direct",
        resource_id=handle,
        kind=ResourceKind.FILE,
        url=f"https://example.test/{handle}.png",
    )
    entry = library.entry(ref)
    entry.path.mkdir(parents=True, exist_ok=True)
    stored = entry.content_path(f"{handle}.png")
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(b"not really a png")
    entry.metadata_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "provider": "direct",
                "key": entry.key,
                "resource_id": handle,
                "parent_id": None,
                "kind": "file",
                "name": f"{handle}.png",
                "source_url": ref.url,
                "source_document": None,
                "status": "completed",
                "discovered_at": None,
                "downloaded_at": datetime(2026, 8, 15, 9, 0, tzinfo=UTC).isoformat(),
                "attempts": 1,
                "error": None,
                "content": {
                    "filename": f"{handle}.png",
                    "path": f"content/{handle}.png",
                    "size": 16,
                    "checksums": [{"algorithm": "sha256", "value": "b" * 64}],
                },
            }
        ),
        encoding="utf-8",
    )


def test_every_run_is_on_the_page(tmp_path: Path) -> None:
    """A script described is a script somebody can find here."""
    with client(tmp_path) as test_client:
        body = test_client.get("/maintenance").text

    for run in RUNS:
        assert run.script in body
        assert run.title in body


def test_the_page_carries_a_line_to_paste(tmp_path: Path) -> None:
    """The command names the interpreter and the settings file of this server."""
    config = tmp_path / "settings.toml"
    with client(tmp_path, config=config) as test_client:
        body = test_client.get("/maintenance").text

    assert "survey_library.py" in body
    assert "--config" in body
    # Rendered into an attribute, so the separator is the escaped one on
    # Windows -- what matters is that the path is in there at all.
    assert "settings.toml" in body


def test_a_server_on_the_defaults_says_so(tmp_path: Path) -> None:
    """Without a settings file there is no --config to print, and none is."""
    with client(tmp_path) as test_client:
        body = test_client.get("/maintenance").text

    assert "--config" not in body
    assert "built-in defaults" in body


def test_apply_appears_only_where_a_run_writes(tmp_path: Path) -> None:
    """One line for a reporting run, two for a writing one."""
    with client(tmp_path) as test_client:
        body = test_client.get("/maintenance").text

    writing = sum(1 for run in RUNS if run.writes)
    assert body.count("--apply") >= writing
    # The survey never writes, so its own line carries no flag.
    survey = body.split("survey_library.py")[1].split("</section>")[0]
    assert "--apply" not in survey


def test_the_page_has_no_form_at_all(tmp_path: Path) -> None:
    """The property the page exists under: nothing here submits anything.

    Not a matter of taste. There is no authentication on this interface
    (ADR-025), so a control that ran one of these would be reachable by anybody
    who can reach the port -- and `start_over.py` moves a whole library aside.
    """
    with client(tmp_path) as test_client:
        body = test_client.get("/maintenance").text

    assert "<form" not in body
    assert 'type="submit"' not in body


def test_the_route_refuses_anything_but_a_get(tmp_path: Path) -> None:
    """And there is no POST route to grow into one."""
    with client(tmp_path) as test_client:
        for method in (test_client.post, test_client.put, test_client.delete):
            assert method("/maintenance").status_code == 405


def test_the_navigation_names_it(tmp_path: Path) -> None:
    """Reachable from every page rather than by knowing the URL."""
    with client(tmp_path) as test_client:
        assert 'href="/maintenance"' in test_client.get("/").text
        assert 'class="active"' in test_client.get("/maintenance").text


def test_the_page_says_what_the_runs_would_act_on(tmp_path: Path) -> None:
    """The library path, which the command itself does not name."""
    with client(tmp_path) as test_client:
        body = test_client.get("/maintenance").text

    assert (tmp_path / "library").as_posix() in body


def test_an_empty_thumbnail_cache_says_so(tmp_path: Path) -> None:
    """Before the maker has ever run, which is the state this page is opened in."""
    with client(tmp_path) as test_client:
        body = test_client.get("/maintenance").text

    assert "no thumbnails made yet" in body


def test_a_filled_thumbnail_cache_is_counted(tmp_path: Path) -> None:
    """What is already cached, and what it takes up.

    The one fact about this installation no other page carries, and the reason
    to think about the cache at all.
    """
    root = cache_beside(tmp_path / "urls.db")
    (root / "ab").mkdir(parents=True)
    (root / "ab" / f"abcdef{SUFFIX}").write_bytes(b"x" * 4096)
    (root / "ab" / f"abbbbb{SUFFIX}").write_bytes(b"x" * 4096)

    with client(tmp_path) as test_client:
        body = test_client.get("/maintenance").text

    assert "no thumbnails made yet" not in body
    assert format_size(8192) in body
    assert root.as_posix() in body


def test_without_the_scripts_the_page_says_where_they_are(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An installation from a wheel has no scripts beside it.

    Naming a file that was never copied would be worse than saying there is
    nothing here to run, so the descriptions stay and the commands go.
    """
    monkeypatch.setattr("maxicrawler.app.maintenance.MARKER", "not_a_file.py")

    with client(tmp_path) as test_client:
        body = test_client.get("/maintenance").text

    # Every description survives; every command is gone, and so is the field
    # that would have held one.
    assert "survey_library.py" in body
    assert "pathbox" not in body
    assert "scripts/" in body


def test_the_library_page_still_copies_its_path(tmp_path: Path) -> None:
    """The other page using the copy script, which now names its field by id."""
    settings = Settings(database_path=tmp_path / "urls.db", library_path=tmp_path / "library")
    library = Library(settings.library_path)
    store_image(library, "AAAA1111")
    service = LibraryService(settings, library=library)
    application = create_app(service=CrawlService(settings), library=service)
    item = service.every()[0]

    with TestClient(application) as test_client:
        page = test_client.get(f"/library/{item.directory}/{item.key}").text

    assert 'id="item-path"' in page
    assert 'data-copy="#item-path"' in page
