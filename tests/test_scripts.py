"""Tests for the maintenance scripts in ``scripts/``.

They are run the way they are used — as programs, in a subprocess, against a
library written by hand — rather than imported and called. What is under test is
the command-line contract: that a plain run changes nothing, that ``--apply``
changes exactly what was announced, and that the record left behind is one the
downloader will not fetch again.

Output is checked for the facts it has to carry, never line by line. A test that
pins the wording turns every improvement to a message into a red build.
"""

import ast
import hashlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from maxicrawler.domain import ResourceKind, ResourceRef
from maxicrawler.library import Library

REPOSITORY = Path(__file__).resolve().parent.parent
SCRIPTS = REPOSITORY / "scripts"

PRUNE = SCRIPTS / "prune_small_payloads.py"
SURVEY = SCRIPTS / "survey_library.py"
CHECK = SCRIPTS / "check_library.py"
START_OVER = SCRIPTS / "start_over.py"
REINDEX = SCRIPTS / "reindex_library.py"

WRITING = {
    "prune_small_payloads.py",
    "check_library.py",
    "start_over.py",
    "reindex_library.py",
}
"""Scripts that can change the library, and must therefore ask with ``--apply``.

Named rather than detected, so that a script gaining the ability to write is a
line changed here by whoever gave it that ability — the test below fails until
it is, which is the point of keeping the list by hand.
"""


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
    payload: bool = True,
    checksum: str | None = None,
) -> str:
    """Write one stored entry of *size* bytes by hand and return its key.

    Written as a document rather than downloaded, for the same reason the
    library service's own tests do it: what is under test reads the shelf, and a
    provider would only add a way for the test to fail.

    The entry is sound unless asked otherwise: the digest really is the digest
    of what was written, so a doctor checking one has nothing to say. *payload*
    and *checksum* are how a test breaks exactly one thing about it.
    """
    ref = ResourceRef(
        provider="direct",
        resource_id=handle,
        kind=ResourceKind.FILE,
        url=f"https://example.test/{handle}/{filename}",
    )
    entry = library.entry(ref)
    entry.path.mkdir(parents=True, exist_ok=True)
    body = b"x" * size
    if payload:
        stored = entry.content_path(filename)
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_bytes(body)
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
            "checksums": [
                {"algorithm": "sha256", "value": checksum or hashlib.sha256(body).hexdigest()}
            ],
        },
        "review": {"verdict": verdict, "favourite": favourite},
    }
    entry.metadata_path.write_text(json.dumps(document), encoding="utf-8")
    return entry.key


def a_mixed_library(tmp_path: Path) -> Library:
    """Return a library holding files either side of a hundred thousand bytes.

    Initialized, so it carries the descriptor a real one has — which is what
    ``start_over.py`` checks before it renames anything.
    """
    library = Library(tmp_path / "library")
    library.initialize()
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
def test_every_script_is_found_by_the_same_config(script: Path, tmp_path: Path) -> None:
    """One way to say which library is meant, spelled the same everywhere."""
    config = settings_file(tmp_path)

    finished = run(script, config, "--help")

    assert finished.returncode == 0, finished.stderr
    assert "--config" in finished.stdout


@pytest.mark.parametrize("script", every_script(), ids=lambda path: path.name)
def test_a_script_asks_before_writing_and_only_if_it_can(script: Path, tmp_path: Path) -> None:
    """Whether ``--apply`` exists says whether the script can change anything.

    Both directions are checked, because both are how somebody gets surprised: a
    destructive script without the flag would do it unasked, and a reporting
    script that offers the flag suggests it might.
    """
    config = settings_file(tmp_path)

    finished = run(script, config, "--help")

    assert ("--apply" in finished.stdout) is (script.name in WRITING)


@pytest.mark.parametrize("script", every_script(), ids=lambda path: path.name)
def test_what_a_script_prints_is_ascii(script: Path) -> None:
    """An em dash arrives on a Windows console as a replacement character.

    These are run in a terminal, and a terminal here is often cp1252 rather than
    UTF-8, where a sentence explaining a refusal turns into one with a black
    diamond in the middle of it. The docstrings keep their typography; the
    strings that reach a screen do not get any. The ``--help`` description is
    checked too, since it is the module docstring's first line.
    """
    tree = ast.parse(script.read_text(encoding="utf-8"))
    offenders: list[str] = []

    doc = ast.get_docstring(tree) or ""
    if doc and not doc.splitlines()[0].isascii():
        offenders.append(f"the --help description: {doc.splitlines()[0]!r}")

    for node in ast.walk(tree):
        printed = isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print"
        helped = isinstance(node, ast.keyword) and node.arg == "help"
        if not (printed or helped):
            continue
        for piece in ast.walk(node):
            if not isinstance(piece, ast.Constant) or not isinstance(piece.value, str):
                continue
            if not piece.value.isascii():
                offenders.append(f"line {piece.lineno}: {piece.value!r}")

    assert not offenders, "\n".join(offenders)


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


# --- surveying what is there --------------------------------------------------


def load(script: Path) -> ModuleType:
    """Import *script* as a module, to reach a function directly."""
    spec = importlib.util.spec_from_file_location(script.stem, script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def png_header(width: int, height: int) -> bytes:
    """Return the opening bytes of a PNG of that size.

    A header rather than an image, because a header is all the survey reads. The
    same goes for the two below.
    """
    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
    )


def gif_header(width: int, height: int) -> bytes:
    return b"GIF89a" + width.to_bytes(2, "little") + height.to_bytes(2, "little") + b"\x00" * 8


def jpeg_header(width: int, height: int, *, padding: int = 0) -> bytes:
    """Return a JPEG whose frame header sits behind *padding* bytes of comment.

    The padding stands in for what really precedes it in a photograph — colour
    profile, EXIF, an embedded preview — and is why the dimensions have to be
    found by walking the segments rather than at a fixed offset.
    """
    comment = b""
    if padding:
        comment = b"\xff\xfe" + (padding + 2).to_bytes(2, "big") + b"\x00" * padding
    frame = (
        b"\xff\xc0"
        + (17).to_bytes(2, "big")
        + b"\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03"
        + b"\x00" * 9
    )
    return b"\xff\xd8" + comment + frame


@pytest.mark.parametrize(
    ("name", "header", "expected"),
    [
        ("a.png", png_header(1920, 1080), (1920, 1080)),
        ("a.gif", gif_header(64, 48), (64, 48)),
        ("a.jpg", jpeg_header(4000, 3000), (4000, 3000)),
        ("far.jpg", jpeg_header(6000, 4000, padding=20_000), (6000, 4000)),
        ("empty.png", b"", None),
        ("not-an-image.bin", b"nothing here", None),
        # Truncated in the middle of the frame header: no answer, not a guess
        # and not an exception.
        ("cut.jpg", jpeg_header(800, 600)[:8], None),
    ],
    # Named, because the parameters themselves are twenty kilobytes of header
    # and pytest puts a generated id into an environment variable.
    ids=["png", "gif", "jpeg", "jpeg-behind-a-big-header", "empty", "not-an-image", "truncated"],
)
def test_dimensions_are_read_from_the_header(
    name: str, header: bytes, expected: tuple[int, int] | None, tmp_path: Path
) -> None:
    survey = load(SURVEY)
    path = tmp_path / name
    path.write_bytes(header)

    assert survey.image_size(path) == expected


def test_a_format_this_does_not_read_is_reported_rather_than_guessed(tmp_path: Path) -> None:
    """A WebP is an image with no dimensions here, and says so."""
    survey = load(SURVEY)
    path = tmp_path / "a.webp"
    path.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 32)

    assert survey.image_size(path) is None


def test_the_survey_counts_what_is_on_the_shelf(tmp_path: Path) -> None:
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    write(library, "photo", filename="photo.jpg", size=400_000)
    write(library, "doc", filename="notes.txt", size=2_000)

    finished = run(SURVEY, config)

    assert finished.returncode == 0, finished.stderr
    assert "2" in finished.stdout
    assert "image" in finished.stdout
    assert "text" in finished.stdout


def test_the_survey_measures_the_images_it_finds(tmp_path: Path) -> None:
    """The number the thumbnail question turns on: pixels, not bytes."""
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    key = write(library, "big", filename="big.png", size=10)
    (library.root / "direct" / key / "content" / "big.png").write_bytes(png_header(6000, 4000))

    finished = run(SURVEY, config)

    assert finished.returncode == 0, finished.stderr
    assert "24.0 MP" in finished.stdout


def test_dimensions_are_reported_in_pixels_not_in_bytes(tmp_path: Path) -> None:
    """Two scales, two units.

    The pixel histogram runs over the same code as the byte one, and reading a
    picture's size as "12.0 MB" when the number counts pixels is a label
    somebody acts on before noticing it is the wrong quantity.
    """
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    key = write(library, "big", filename="big.png", size=10)
    (library.root / "direct" / key / "content" / "big.png").write_bytes(png_header(6000, 4000))

    finished = run(SURVEY, config)

    dimensions = finished.stdout.split("Image dimensions")[1]
    assert "MP" in dimensions
    assert "MB" not in dimensions.split("largest")[0]


def test_a_discarded_entry_is_counted_as_a_record_and_not_as_a_file(tmp_path: Path) -> None:
    """A headstone goes on claiming a payload, and the survey must not believe it.

    ``is_stored`` asks whether the *record* says there is a file, which after a
    discard is still yes — that is what keeps the entry searchable and unfetched
    (ADR-041). Counting it as stored would report space that is already free.
    """
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    write(library, "kept", filename="kept.jpg", size=400_000)
    write(library, "gone", filename="gone.jpg", size=999_000_000, verdict="discarded")

    finished = run(SURVEY, config)

    assert finished.returncode == 0, finished.stderr
    header = finished.stdout.split("By type")[0]
    assert "1 hold a file" in header.replace(",", "")
    # The discarded entry is present as itself, and its bytes are not in the
    # total.
    assert "discarded" in finished.stdout
    assert "999" not in header


def test_the_cost_of_a_tile_page_is_counted_from_the_images_shown_at_full_size(
    tmp_path: Path,
) -> None:
    """The number neither histogram gives on its own.

    A small file can be a large picture: what is sent and what the browser then
    holds differ by the compression. Only the images below the byte limit are
    counted, because those are the ones a tile loads as they are.
    """
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    library.initialize()
    small_file_big_picture = write(library, "sneaky", filename="sneaky.png", size=900_000)
    (entry_path(library, small_file_big_picture) / "content" / "sneaky.png").write_bytes(
        png_header(6000, 4000)
    )
    # Over the limit, so a tile shows a symbol for it and it is not counted.
    heavy = write(library, "heavy", filename="heavy.png", size=8_000_000)
    (entry_path(library, heavy) / "content" / "heavy.png").write_bytes(png_header(8000, 8000))

    finished = run(SURVEY, config)

    assert finished.returncode == 0, finished.stderr
    page = finished.stdout.split("What a page of")[1]
    assert "1 of 1 are over 4 MP" in page
    # 24 megapixels at four bytes each, and only the one that is shown at full
    # size: 96 MB, not the 256 MB the other one would add.
    assert "96.0 MB" in page


def test_the_measuring_can_be_left_out(tmp_path: Path) -> None:
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    key = write(library, "big", filename="big.png", size=10)
    (library.root / "direct" / key / "content" / "big.png").write_bytes(png_header(6000, 4000))

    finished = run(SURVEY, config, "--skip-dimensions")

    assert finished.returncode == 0, finished.stderr
    assert "MP" not in finished.stdout


def test_an_empty_library_surveys_to_nothing_rather_than_to_an_error(tmp_path: Path) -> None:
    config = settings_file(tmp_path)

    finished = run(SURVEY, config)

    assert finished.returncode == 0, finished.stderr


# --- checking the shelf against the disk --------------------------------------


def entry_path(library: Library, key: str) -> Path:
    """Return the directory of the entry under *key*."""
    return library.root / "direct" / key


def test_a_sound_library_has_nothing_to_report(tmp_path: Path) -> None:
    """Including under ``--checksums``, which is the stricter reading."""
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    write(library, "one", filename="one.jpg", size=5_000)
    write(library, "two", filename="two.png", size=9_000)

    finished = run(CHECK, config, "--checksums")

    assert finished.returncode == 0, finished.stderr
    assert "Nothing to report" in finished.stdout


def test_a_record_pointing_at_a_missing_file_is_reported_with_its_url(tmp_path: Path) -> None:
    """The one fault that is worth acting on, so the URL is worth printing."""
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    write(library, "lost", filename="lost.jpg", size=5_000, payload=False)

    finished = run(CHECK, config, "--urls")

    assert finished.returncode == 0, finished.stderr
    assert "not there" in finished.stdout
    assert "https://example.test/lost/lost.jpg" in finished.stdout


def test_a_file_of_the_wrong_size_is_reported(tmp_path: Path) -> None:
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    key = write(library, "grown", filename="grown.jpg", size=5_000)
    (entry_path(library, key) / "content" / "grown.jpg").write_bytes(b"y" * 9_000)

    finished = run(CHECK, config)

    assert finished.returncode == 0, finished.stderr
    assert "not the size its record gives" in finished.stdout
    assert "5.0 KB" in finished.stdout
    assert "9.0 KB" in finished.stdout


def test_a_changed_file_is_only_caught_when_the_checksums_are_asked_for(tmp_path: Path) -> None:
    """Reading every byte of a library is not something to do unasked."""
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    write(library, "tampered", filename="changed.jpg", size=5_000, checksum="f" * 64)

    quiet = run(CHECK, config)
    assert "checksum" not in quiet.stdout

    asked = run(CHECK, config, "--checksums")
    assert asked.returncode == 0, asked.stderr
    assert "does not match its recorded checksum" in asked.stdout


def test_a_file_no_record_mentions_is_reported(tmp_path: Path) -> None:
    """What a re-download under a new name leaves behind."""
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    key = write(library, "renamed", filename="new.jpg", size=5_000)
    (entry_path(library, key) / "content" / "old.jpg").write_bytes(b"z" * 3_000)

    finished = run(CHECK, config)

    assert finished.returncode == 0, finished.stderr
    assert "no record mentions" in finished.stdout
    assert "old.jpg" in finished.stdout


def test_an_unreadable_document_is_reported_and_not_treated_as_absent(tmp_path: Path) -> None:
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    key = write(library, "broken", filename="fine.jpg", size=5_000)
    (entry_path(library, key) / "metadata.json").write_text("{ not json", encoding="utf-8")

    finished = run(CHECK, config)

    assert finished.returncode == 0, finished.stderr
    assert "could not be read" in finished.stdout


def test_a_directory_with_content_and_no_document_is_reported(tmp_path: Path) -> None:
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    stray = library.root / "direct" / "no-document-here" / "content"
    stray.mkdir(parents=True)
    (stray / "stray.bin").write_bytes(b"w" * 700)

    finished = run(CHECK, config)

    assert finished.returncode == 0, finished.stderr
    assert "holds no metadata document" in finished.stdout


def test_applying_clears_what_an_interrupted_download_left(tmp_path: Path) -> None:
    """A staged file is worthless the moment the transfer stopped (ADR-012)."""
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    key = write(library, "stopped", filename="half.zip", size=5_000)
    staged = entry_path(library, key) / ".incomplete"
    staged.mkdir()
    (staged / "half.zip").write_bytes(b"q" * 120_000)

    reported = run(CHECK, config)
    assert "left something behind" in reported.stdout
    assert (staged / "half.zip").exists(), "a plain run must not delete anything"

    finished = run(CHECK, config, "--apply")

    assert finished.returncode == 0, finished.stderr
    assert not (staged / "half.zip").exists()


def test_applying_finishes_a_discard_whose_file_is_still_there(tmp_path: Path) -> None:
    """The intention is already on record; carrying it out is not a new decision."""
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    key = write(library, "undead", filename="gone.png", size=2_000, verdict="discarded")

    finished = run(CHECK, config, "--apply")

    assert finished.returncode == 0, finished.stderr
    assert not (entry_path(library, key) / "content" / "gone.png").exists()
    review = document_of(library, key)["review"]
    assert isinstance(review, dict)
    assert review["payload_removed_at"], "the removal has to be written down, not just done"


def test_applying_leaves_everything_it_did_not_decide_alone(tmp_path: Path) -> None:
    """The promise the whole script rests on.

    A file no record mentions might be something somebody put there; a record
    whose payload is gone might be worth fetching again. Both would need a new
    decision, and this is a maintenance script, not the person who owns the
    library.
    """
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    unclaimed = write(library, "renamed", filename="new.jpg", size=5_000)
    stray = entry_path(library, unclaimed) / "content" / "old.jpg"
    stray.write_bytes(b"z" * 3_000)
    lost = write(library, "lost", filename="lost.jpg", size=5_000, payload=False)

    finished = run(CHECK, config, "--apply")

    assert finished.returncode == 0, finished.stderr
    assert stray.read_bytes() == b"z" * 3_000
    record = document_of(library, lost)
    assert record["content"], "a record whose file is gone is left as it was"
    assert record["review"] == {"verdict": "unreviewed", "favourite": False}


def test_starting_over_moves_the_library_aside_rather_than_deleting_it(tmp_path: Path) -> None:
    """The whole design: afterwards everything is still there, under a new name."""
    config = settings_file(tmp_path)
    library = a_mixed_library(tmp_path)
    before = shelf_contents(library)

    finished = run(START_OVER, config, "--apply")

    assert finished.returncode == 0, finished.stderr
    moved = [path for path in tmp_path.glob("library.*") if path.is_dir()]
    assert len(moved) == 1, "the old library is set aside under one timestamped name"
    kept = {
        str(path.relative_to(moved[0])): path.read_bytes()
        for path in sorted(moved[0].rglob("*"))
        if path.is_file()
    }
    assert kept == before
    # And what takes its place is a library, not a hole: a descriptor, and
    # nothing else at all.
    assert (library.root / "library.json").is_file()
    assert [path.name for path in library.root.iterdir()] == ["library.json"]


def test_starting_over_says_how_to_change_your_mind(tmp_path: Path) -> None:
    config = settings_file(tmp_path)
    a_mixed_library(tmp_path)

    finished = run(START_OVER, config, "--apply")

    assert "rename these back" in finished.stdout


def test_nothing_of_the_old_database_is_left_beside_the_new_library(tmp_path: Path) -> None:
    """Not the database, and not the write-ahead log that belonged to it.

    A log beside a database that has moved is worse than one beside nothing, and
    which of the two ways it goes is not worth pinning down: the script renames
    it, or SQLite collects an orphaned one while the script is checking whether
    anything holds the database open. What matters is that neither is still
    sitting there when the next run starts.
    """
    config = settings_file(tmp_path)
    a_mixed_library(tmp_path)
    (tmp_path / "maxicrawler.db").write_bytes(b"SQLite format 3\x00")
    (tmp_path / "maxicrawler.db-wal").write_bytes(b"log")

    finished = run(START_OVER, config, "--apply")

    assert finished.returncode == 0, finished.stderr
    assert not (tmp_path / "maxicrawler.db").exists()
    assert not (tmp_path / "maxicrawler.db-wal").exists()
    assert len(list(tmp_path.glob("maxicrawler.db.*"))) == 1


def test_a_directory_that_is_not_a_library_is_refused(tmp_path: Path) -> None:
    """A mistyped path moves nothing.

    The descriptor is the only thing that says a directory is one of ours, and
    this is the one script that acts on a whole library at once.
    """
    config = settings_file(tmp_path)
    somewhere_else = tmp_path / "library"
    somewhere_else.mkdir()
    (somewhere_else / "important.txt").write_text("not ours", encoding="utf-8")

    finished = run(START_OVER, config, "--apply")

    assert finished.returncode == 1
    assert "not a library" in finished.stdout
    assert (somewhere_else / "important.txt").exists()


def test_a_busy_database_stops_it(tmp_path: Path) -> None:
    """Standing in for a running server, which must be stopped first."""
    config = settings_file(tmp_path)
    library = a_mixed_library(tmp_path)
    database = tmp_path / "maxicrawler.db"
    holding = sqlite3.connect(database)
    holding.execute("CREATE TABLE IF NOT EXISTS t (x)")
    holding.execute("BEGIN IMMEDIATE")
    try:
        finished = run(START_OVER, config, "--apply")
    finally:
        holding.rollback()
        holding.close()

    assert finished.returncode == 1
    assert "running server" in finished.stdout
    assert library.root.is_dir(), "nothing is renamed while something holds the database"
    assert not list(tmp_path.glob("library.*"))


def test_the_index_can_be_thrown_away_and_nothing_is_lost(tmp_path: Path) -> None:
    """The claim ADR-037 makes, carried out.

    If anything about the library were only in the cache, a listing taken after
    the rows are dropped would differ from one taken before. It does not, and
    that is the whole of what a cache means.
    """
    config = settings_file(tmp_path)
    a_mixed_library(tmp_path)
    before = run(SURVEY, config, "--skip-dimensions")

    rebuilt = run(REINDEX, config, "--apply")
    assert rebuilt.returncode == 0, rebuilt.stderr

    after = run(SURVEY, config, "--skip-dimensions")
    assert after.stdout == before.stdout


def test_reindexing_leaves_the_rest_of_the_database_alone(tmp_path: Path) -> None:
    """The reason this drops rows instead of removing a file.

    The crawl history and the URLs discovery has seen live in the same database
    and can be rebuilt from nothing at all.
    """
    config = settings_file(tmp_path)
    a_mixed_library(tmp_path)
    run(REINDEX, config)  # so the database exists with its own tables

    planted = sqlite3.connect(tmp_path / "maxicrawler.db")
    planted.execute("CREATE TABLE IF NOT EXISTS crawl_sessions (id TEXT PRIMARY KEY, seed TEXT)")
    planted.execute("INSERT INTO crawl_sessions VALUES ('c1', 'https://example.test/')")
    planted.commit()
    planted.close()

    finished = run(REINDEX, config, "--apply")

    assert finished.returncode == 0, finished.stderr
    reading = sqlite3.connect(tmp_path / "maxicrawler.db")
    try:
        assert reading.execute("SELECT seed FROM crawl_sessions").fetchall() == [
            ("https://example.test/",)
        ]
    finally:
        reading.close()


def test_a_row_that_lies_is_put_right(tmp_path: Path) -> None:
    """What the script is actually for.

    An ordinary listing re-reads a document whose timestamp or length changed.
    A library copied between machines can carry rows whose stamps still match
    while the contents no longer do, and only dropping them fixes that.
    """
    config = settings_file(tmp_path)
    library = a_mixed_library(tmp_path)
    run(SURVEY, config, "--skip-dimensions")  # fills the cache

    lying = sqlite3.connect(tmp_path / "maxicrawler.db")
    try:
        document = (tmp_path / "library" / "direct").glob("*/metadata.json")
        record = json.loads(next(document).read_text(encoding="utf-8"))
        record["name"] = "a name no document on disk has"
        changed = lying.execute(
            "UPDATE library_entries SET document = ? WHERE key = ?",
            (json.dumps(record), record["key"]),
        )
        # Without this the test would pass on an UPDATE that matched nothing,
        # which is to say it would prove the script fixes a fault it was never
        # shown.
        assert changed.rowcount == 1
        lying.commit()
    finally:
        lying.close()

    misled = run(SURVEY, config, "--skip-dimensions")
    assert misled.returncode == 0, misled.stderr

    run(REINDEX, config, "--apply")
    honest = run(SURVEY, config, "--skip-dimensions")

    assert honest.returncode == 0, honest.stderr
    assert (library.root / "direct").is_dir()
    reading = sqlite3.connect(tmp_path / "maxicrawler.db")
    try:
        names = [
            json.loads(row[0])["name"]
            for row in reading.execute("SELECT document FROM library_entries").fetchall()
        ]
    finally:
        reading.close()
    assert "a name no document on disk has" not in names


def test_an_absent_library_is_nothing_to_do(tmp_path: Path) -> None:
    config = settings_file(tmp_path)

    finished = run(START_OVER, config, "--apply")

    assert finished.returncode == 0, finished.stderr
    assert "no library at that path" in finished.stdout


def test_a_second_run_finds_the_repairs_done(tmp_path: Path) -> None:
    config = settings_file(tmp_path)
    library = Library(tmp_path / "library")
    key = write(library, "stopped", filename="half.zip", size=5_000)
    staged = entry_path(library, key) / ".incomplete"
    staged.mkdir()
    (staged / "half.zip").write_bytes(b"q" * 1_000)

    run(CHECK, config, "--apply")
    again = run(CHECK, config)

    assert again.returncode == 0, again.stderr
    assert "Nothing to report" in again.stdout
