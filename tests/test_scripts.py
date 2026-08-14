"""Tests for the maintenance scripts in ``scripts/``.

They are run the way they are used — as programs, in a subprocess, against a
library written by hand — rather than imported and called. What is under test is
the command-line contract: that a plain run changes nothing, that ``--apply``
changes exactly what was announced, and that the record left behind is one the
downloader will not fetch again.

Output is checked for the facts it has to carry, never line by line. A test that
pins the wording turns every improvement to a message into a red build.
"""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from maxicrawler.domain import ResourceKind, ResourceRef
from maxicrawler.library import Library

REPOSITORY = Path(__file__).resolve().parent.parent
SCRIPTS = REPOSITORY / "scripts"

PRUNE = SCRIPTS / "prune_small_payloads.py"


def every_script() -> list[Path]:
    """Return every script in the directory, so the shared rules cover new ones.

    A script added without a thought about the contract below fails these tests
    on the way in, which is the point of collecting them by directory rather
    than by name.
    """
    return sorted(path for path in SCRIPTS.glob("*.py") if not path.name.startswith("_"))


def settings_file(tmp_path: Path) -> Path:
    """Write a settings file pointing at a library below *tmp_path*."""
    path = tmp_path / "settings.toml"
    path.write_text(
        "[maxicrawler]\n"
        f'library_path = "{(tmp_path / "library").as_posix()}"\n'
        f'database_path = "{(tmp_path / "maxicrawler.db").as_posix()}"\n',
        encoding="utf-8",
    )
    return path


def run(script: Path, config: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run *script* the way somebody at a shell would."""
    return subprocess.run(
        [sys.executable, str(script), "--config", str(config), *arguments],
        capture_output=True,
        text=True,
        check=False,
        cwd=config.parent,
    )


def write(
    library: Library,
    handle: str,
    *,
    filename: str,
    size: int,
    favourite: bool = False,
    verdict: str = "unreviewed",
) -> str:
    """Write one stored entry of *size* bytes by hand and return its key.

    Written as a document rather than downloaded, for the same reason the
    library service's own tests do it: what is under test reads the shelf, and a
    provider would only add a way for the test to fail.
    """
    ref = ResourceRef(
        provider="direct",
        resource_id=handle,
        kind=ResourceKind.FILE,
        url=f"https://example.test/{filename}",
    )
    entry = library.entry(ref)
    entry.path.mkdir(parents=True, exist_ok=True)
    stored = entry.content_path(filename)
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(b"x" * size)
    document = {
        "schema": 1,
        "provider": "direct",
        "key": entry.key,
        "resource_id": handle,
        "parent_id": None,
        "kind": "file",
        "name": filename,
        "source_url": ref.url,
        "source_document": None,
        "status": "completed",
        "discovered_at": None,
        "downloaded_at": datetime(2026, 8, 9, 14, 30, tzinfo=UTC).isoformat(),
        "attempts": 1,
        "error": None,
        "content": {
            "filename": filename,
            "path": f"content/{filename}",
            "size": size,
            "checksums": [{"algorithm": "sha256", "value": "0" * 64}],
        },
        "review": {"verdict": verdict, "favourite": favourite},
    }
    entry.metadata_path.write_text(json.dumps(document), encoding="utf-8")
    return entry.key


def a_mixed_library(tmp_path: Path) -> Library:
    """Return a library holding files either side of a hundred thousand bytes."""
    library = Library(tmp_path / "library")
    write(library, "small", filename="icon.png", size=4_000)
    write(library, "exactly", filename="exactly.jpg", size=100_000)
    write(library, "large", filename="photo.jpg", size=400_000)
    write(library, "starred", filename="starred-icon.png", size=5_000, favourite=True)
    return library


def shelf_contents(library: Library) -> dict[str, bytes]:
    """Return every file below the library, by path relative to its root."""
    return {
        str(path.relative_to(library.root)): path.read_bytes()
        for path in sorted(library.root.rglob("*"))
        if path.is_file()
    }


def document_of(library: Library, key: str) -> dict[str, object]:
    """Return the metadata document of the entry under *key*."""
    path = library.root / "direct" / key / "metadata.json"
    loaded: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


# --- what every script promises -----------------------------------------------


@pytest.mark.parametrize("script", every_script(), ids=lambda path: path.name)
def test_every_script_takes_the_same_two_arguments(script: Path, tmp_path: Path) -> None:
    """``--config`` to find the library, ``--apply`` to be allowed to touch it.

    Spelled the same everywhere, so that knowing one of these scripts is most of
    knowing the next.
    """
    config = settings_file(tmp_path)

    finished = run(script, config, "--help")

    assert finished.returncode == 0, finished.stderr
    assert "--config" in finished.stdout
    assert "--apply" in finished.stdout


@pytest.mark.parametrize("script", every_script(), ids=lambda path: path.name)
def test_every_script_runs_on_a_library_and_writes_nothing(script: Path, tmp_path: Path) -> None:
    """A plain run is a report.

    The shared rule of this directory, and the one worth a test per script: with
    no ``--apply``, the library is byte for byte what it was. The database is
    left out of the comparison on purpose — it is a cache the listing fills as it
    reads (ADR-037), and it changing is not the library changing.
    """
    config = settings_file(tmp_path)
    library = a_mixed_library(tmp_path)
    before = shelf_contents(library)

    finished = run(script, config)

    assert finished.returncode == 0, finished.stderr
    assert shelf_contents(library) == before


# --- pruning small files ------------------------------------------------------


def test_a_plain_run_names_what_it_would_throw_away(tmp_path: Path) -> None:
    config = settings_file(tmp_path)
    a_mixed_library(tmp_path)

    finished = run(PRUNE, config)

    assert finished.returncode == 0, finished.stderr
    assert "icon.png" in finished.stdout
    # The exact byte count beside the rounded size, because the decision is a
    # byte comparison and "100.0 KB" would read as a file that should have been
    # kept.
    assert "4,000" in finished.stdout
    assert "photo.jpg" not in finished.stdout


def test_applying_removes_the_file_and_keeps_the_record(tmp_path: Path) -> None:
    """The whole reason this goes through the service and not through ``rm``."""
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    key = write(library, "small", filename="icon.png", size=4_000)

    finished = run(PRUNE, config, "--apply")

    assert finished.returncode == 0, finished.stderr
    assert not (library.root / "direct" / key / "content" / "icon.png").exists()
    document = document_of(library, key)
    review = document["review"]
    assert isinstance(review, dict)
    assert review["verdict"] == "discarded"
    assert review["payload_removed_at"]
    # The headstone still says what it was, which is what makes a discarded
    # entry searchable rather than merely absent.
    content = document["content"]
    assert isinstance(content, dict)
    assert content["filename"] == "icon.png"
    assert content["size"] == 4_000


def test_a_file_of_exactly_the_limit_is_kept(tmp_path: Path) -> None:
    """The boundary belongs to the file: *below* the size goes, not *at* it."""
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    key = write(library, "exactly", filename="exactly.jpg", size=100_000)

    finished = run(PRUNE, config, "--apply")

    assert finished.returncode == 0, finished.stderr
    assert (library.root / "direct" / key / "content" / "exactly.jpg").exists()


def test_a_favourite_is_left_alone_until_it_is_asked_for(tmp_path: Path) -> None:
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    key = write(library, "starred", filename="starred-icon.png", size=5_000, favourite=True)
    stored = library.root / "direct" / key / "content" / "starred-icon.png"

    kept = run(PRUNE, config, "--apply")
    assert kept.returncode == 0, kept.stderr
    assert stored.exists()

    asked = run(PRUNE, config, "--apply", "--include-favourites")
    assert asked.returncode == 0, asked.stderr
    assert not stored.exists()


def test_the_size_to_prune_below_can_be_given(tmp_path: Path) -> None:
    """``--min-size`` overrides the setting, for a pass with a different idea."""
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    key = write(library, "large", filename="photo.jpg", size=400_000)

    finished = run(PRUNE, config, "--min-size", "500000", "--apply")

    assert finished.returncode == 0, finished.stderr
    assert not (library.root / "direct" / key / "content" / "photo.jpg").exists()


def test_running_it_twice_finds_nothing_the_second_time(tmp_path: Path) -> None:
    """Discarded entries are not offered again, including to this script."""
    config = settings_file(tmp_path)
    a_mixed_library(tmp_path)

    run(PRUNE, config, "--apply")
    again = run(PRUNE, config)

    assert again.returncode == 0, again.stderr
    assert "icon.png" not in again.stdout


def test_a_limit_of_zero_does_nothing(tmp_path: Path) -> None:
    """The same off switch ``min_download_size`` has (ADR-042)."""
    config = settings_file(tmp_path)
    library = a_mixed_library(tmp_path)
    before = shelf_contents(library)

    finished = run(PRUNE, config, "--min-size", "0", "--apply")

    assert finished.returncode == 0, finished.stderr
    assert shelf_contents(library) == before
