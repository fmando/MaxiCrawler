"""Set the library and its database aside, leaving an empty one to start from.

For when a crawl went somewhere unintended and the shelf is worth abandoning
rather than sorting through.

**It renames rather than deletes**, and that is the whole design. Emptying a
library is two lines at a shell; what a script adds is not the deleting, it is
the not-deleting. Both are moved aside under a timestamp, an empty library is
put in their place, and the commands to change your mind are printed. The disk
is still full afterwards — putting that right is a second, deliberate act by
somebody who has already seen it work.

Three things are checked first, because this is the one script here that acts on
a whole library at once:

* the library is one of ours, by its ``library.json`` descriptor — so a
  mistyped path moves nothing;
* nothing already sits at the names it would move to;
* the database opens for writing, which a running server would usually deny.
  That last one is a hint rather than a guarantee: on Linux a file can be
  renamed while it is open, and a server still writing into a database that has
  been moved out from under it will do neither of you any good. Stop the server.

Usage::

    python scripts/start_over.py --config settings.toml
    python scripts/start_over.py --config settings.toml --apply
"""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shelf import parser_for, settings_from  # noqa: E402

from maxicrawler.library import DESCRIPTOR_FILENAME, Library  # noqa: E402
from maxicrawler.utils import format_size  # noqa: E402

DATABASE_PARTS = ("", "-wal", "-shm")
"""The suffixes SQLite may have left beside the database file.

Moved with it rather than left behind: a write-ahead log next to a database that
is no longer there is a puzzle for whoever finds it, and one belonging to a
database that has moved is worse than that.
"""


def stamp(when: datetime) -> str:
    """Return the suffix moved-aside names carry.

    Colons are not usable in a filename on Windows, so the time is written
    without them rather than in the ISO form the records use.
    """
    return when.strftime("%Y-%m-%d-%H%M%S")


def weight(path: Path) -> tuple[int, int]:
    """Return how many files are below *path* and how many bytes they hold."""
    files = 0
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                files += 1
                total += child.stat().st_size
        except OSError:
            continue
    return files, total


def database_is_busy(path: Path) -> bool:
    """Return whether something else appears to be holding the database.

    Asked by taking the write lock for a moment and letting it go again. A
    server between requests will not be caught by this, which is why the answer
    is only ever used to refuse, never to reassure.
    """
    if not path.exists():
        return False
    # Closed by hand rather than with a `with` block, which for sqlite3 opens a
    # transaction and does not close the connection. Leaving it open would hold
    # the file, and on Windows the rename this is checking for would then fail
    # because of the check itself.
    connection = sqlite3.connect(path, timeout=0.5)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()
    except sqlite3.OperationalError:
        return True
    except sqlite3.DatabaseError:
        # Not a database this can read, which is a different problem and not
        # one that should stop a rename.
        return False
    finally:
        connection.close()
    return False


def plan_moves(library_path: Path, database_path: Path, suffix: str) -> list[tuple[Path, Path]]:
    """Return what would be renamed to what, given how things stand right now.

    Worked out again immediately before the renaming rather than reused from the
    listing, because opening the database to see whether anything holds it can
    itself tidy an orphaned write-ahead log away. Between saying what is there
    and moving it, something has to look.
    """
    moves = [(library_path, library_path.with_name(f"{library_path.name}.{suffix}"))]
    for part in DATABASE_PARTS:
        beside = database_path.with_name(database_path.name + part)
        if beside.exists():
            moves.append((beside, beside.with_name(f"{beside.name}.{suffix}")))
    return moves


def main() -> int:
    """Move the library and database aside, or say what that would mean."""
    parser = parser_for(__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually move them aside; without it nothing is renamed",
    )
    args = parser.parse_args()
    settings = settings_from(args.config)
    library_path = settings.library_path
    database_path = settings.database_path
    suffix = stamp(datetime.now())

    print(f"Library:  {library_path}")
    print(f"Database: {database_path}")

    if not library_path.is_dir():
        print("\nNothing to do: there is no library at that path.")
        return 0
    if not (library_path / DESCRIPTOR_FILENAME).is_file():
        print(
            f"\nRefusing: {library_path} holds no {DESCRIPTOR_FILENAME},"
            " so it is not a library this wrote."
        )
        return 1

    moves = plan_moves(library_path, database_path, suffix)
    taken = [destination for _, destination in moves if destination.exists()]
    if taken:
        print(f"\nRefusing: {taken[0]} is already there.")
        return 1

    files, total = weight(library_path)
    print(f"\nWould move aside {files:,} files, {format_size(total)}:")
    for source, destination in moves:
        print(f"  {source.name}  ->  {destination.name}")
    print("\nAn empty library takes their place. Nothing is deleted; the disk stays as full.")

    if not args.apply:
        print("\nNothing was written. Run again with --apply to do it.")
        return 0

    if database_is_busy(database_path):
        print(
            "\nRefusing: something else is using the database, most likely a running"
            " server. Stop it first. A server writing into a database that has been"
            " moved out from under it helps nobody."
        )
        return 1

    moved: list[tuple[Path, Path]] = []
    for source, destination in plan_moves(library_path, database_path, suffix):
        try:
            source.rename(destination)
        except OSError as error:
            print(f"\nStopped at {source.name}: {error}")
            print("Anything already moved is still there under its new name:")
            for was, now in moved:
                print(f"  {now.name}  ->  {was.name}")
            return 1
        moved.append((source, destination))

    Library(library_path).initialize()
    print(f"\nMoved aside. A new empty library is at {library_path}.")
    print("To change your mind, remove the new one and rename these back:")
    for source, destination in moved:
        print(f"  {destination.name}  ->  {source.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
