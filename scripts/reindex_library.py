"""Throw the listing cache away and read the library's own documents again.

The directories are the authority and the table in the database is a cache of
what they say (ADR-037). This is what makes that claim checkable: drop every
cached row and let the next listing rebuild it from the documents. If anything
were only in the cache, it would be gone afterwards. Nothing is.

Worth doing after moving a library between machines, where the recorded
modification times may no longer match what is beside them, or after a crash
left the two disagreeing. Not worth doing routinely: an ordinary listing already
re-reads any document whose timestamp or length has changed.

**Only the library's own rows go.** The same database file holds the crawl
history and the URLs discovery has seen, and neither can be rebuilt from
anything — deleting the file to refresh a cache would throw those away with it.
That is why this drops rows rather than removing a database.

Usage::

    python scripts/reindex_library.py --config settings.toml
    python scripts/reindex_library.py --config settings.toml --apply
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shelf import parser_for, settings_from  # noqa: E402

from maxicrawler.app import LibraryService  # noqa: E402

# How one library addresses its rows in the shared table. Imported rather than
# spelled out again: the rule is that two spellings of one path are one library,
# and a second copy of it here would be a second chance to get that wrong.
from maxicrawler.app.library import _root_key  # noqa: E402
from maxicrawler.database import SQLiteDatabase, SQLiteLibraryIndex  # noqa: E402
from maxicrawler.library import Library  # noqa: E402


def cached_rows(index: SQLiteLibraryIndex, root: str) -> int:
    """Return how many rows the cache holds for the library at *root*."""
    return len(index.entries(root))


def stored_entries(library: Library) -> int:
    """Return how many entry directories the library has."""
    return sum(1 for _ in library.entries())


def main() -> int:
    """Rebuild the cache, or say what rebuilding it would mean."""
    parser = parser_for(__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually drop the rows and read the documents again",
    )
    args = parser.parse_args()
    settings = settings_from(args.config)
    library = Library(settings.library_path)
    root = _root_key(library.root)

    index = SQLiteLibraryIndex(SQLiteDatabase(settings.database_path))
    index.initialize()

    print(f"Library:  {settings.library_path}")
    print(f"Database: {settings.database_path}")
    print(f"Indexed:  {cached_rows(index, root):,} rows for this library")
    print(f"On disk:  {stored_entries(library):,} entries")

    if not args.apply:
        print("\nWould drop those rows and read every document again.")
        print("The crawl history and the discovered URLs share this file and stay as they are.")
        print("\nNothing was written. Run again with --apply to do it.")
        return 0

    index.forget(root)
    print(f"\nDropped the cached rows; {cached_rows(index, root):,} remain.")

    # One listing rebuilds the whole cache: reading a page synchronizes every
    # entry, not the fifty it is about to show. What the page itself holds is of
    # no interest here — and is a smaller number, since a listing leaves out
    # what was discarded while the cache keeps it.
    LibraryService(settings, library=library).browse()
    print(
        f"Read the library again: {cached_rows(index, root):,} rows"
        f" for {stored_entries(library):,} entries on disk."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
