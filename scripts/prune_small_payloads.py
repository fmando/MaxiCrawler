"""Discard stored files below a size, the way the interface would.

`min_download_size` keeps small files out of the library from now on (ADR-042).
It says nothing about what is already in there, which is what this is for: the
thumbnails, sprites and icons an image directory answered with before the floor
existed.

**It discards rather than deletes, and the difference is the whole point.**
Removing the files with `rm` would leave every one of those entries claiming a
payload that is not there — which the file's page reports as damage, because it
cannot tell that apart from a file somebody moved — and the next "queue every
match" would fetch all of them again, since "the library holds this" is answered
by the record *and* the file. Going through `LibraryService.discard` writes the
headstone in the same step, so the entry stays searchable, says plainly that it
was thrown away, and is not offered again until that is taken back (ADR-041).

**Nothing happens without `--apply`.** The default run prints what it would
throw away and what that frees, because a list of nine hundred files is worth
reading once before it is worth acting on.

Usage::

    python scripts/prune_small_payloads.py --config settings.toml
    python scripts/prune_small_payloads.py --config settings.toml --apply
"""

import argparse
import sys
from pathlib import Path

# Run from a checkout without installing it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from maxicrawler.app import LibraryItem, LibraryQuery, LibraryService  # noqa: E402
from maxicrawler.config import Settings  # noqa: E402
from maxicrawler.domain import ReviewVerdict  # noqa: E402
from maxicrawler.utils import format_size  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Return what was asked for on the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="settings file to read the library path from; the defaults otherwise",
    )
    parser.add_argument(
        "--min-size",
        type=int,
        default=None,
        help="keep files of at least this many bytes (default: min_download_size)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually discard; without it nothing is written",
    )
    parser.add_argument(
        "--include-favourites",
        action="store_true",
        help="throw away starred files too, which are otherwise left alone",
    )
    return parser.parse_args()


def every_item(shelf: LibraryService) -> list[LibraryItem]:
    """Return every entry in the library, page by page.

    Collected before anything is discarded rather than acted on while paging:
    each discard changes what page two holds, and a listing that moves under a
    loop is how half of a set gets skipped.
    """
    items: list[LibraryItem] = []
    page_number = 1
    while True:
        page = shelf.browse(LibraryQuery(page=page_number, per_page=500))
        items.extend(page.items)
        if page_number >= page.pages:
            return items
        page_number += 1


def too_small(item: LibraryItem, *, limit: int, favourites: bool) -> bool:
    """Return whether *item* is one of the ones to throw away."""
    if not item.is_stored or item.verdict is ReviewVerdict.DISCARDED:
        # Nothing to take back: the record claims no payload, or its payload
        # has already gone.
        return False
    if item.favourite and not favourites:
        return False
    # A size nobody recorded is not a small size — the same rule the listing
    # sorts by. It is left alone and reported at the end.
    return item.size is not None and item.size < limit


def main() -> int:
    """Print what would go, or throw it away when asked to."""
    args = parse_args()
    settings = Settings.from_toml(args.config) if args.config else Settings()
    limit = args.min_size if args.min_size is not None else settings.min_download_size
    if limit <= 0:
        print("Nothing to do: the size to prune below is zero or less.")
        return 0

    shelf = LibraryService(settings)
    items = every_item(shelf)
    doomed = [
        too for too in items if too_small(too, limit=limit, favourites=args.include_favourites)
    ]
    unknown = sum(1 for item in items if item.is_stored and item.size is None)

    print(f"Library:  {settings.library_path}")
    print(f"Entries:  {len(items)}, of which {len(doomed)} are under {format_size(limit)}")
    if unknown:
        print(f"Unsized:  {unknown} stored entries record no size and are left alone")
    if not doomed:
        return 0

    freed = sum(item.size or 0 for item in doomed)
    for item in doomed:
        # The exact count beside the rounded one, because the decision *is* a
        # byte comparison: 99,999 bytes prints as "100.0 KB" and would read as
        # a file that should have been kept.
        size = item.size or 0
        print(f"  {format_size(size):>10} {size:>10,}  {item.directory}/{item.key}  {item.name}")
    print(f"\n{len(doomed)} files, {format_size(freed)}")

    if not args.apply:
        print("\nNothing was written. Run again with --apply to discard these.")
        return 0

    gone = 0
    for item in doomed:
        if shelf.discard(item.directory, item.key) is None:
            # A file another program is holding open, which on Windows is
            # ordinary. The entry is untouched, and saying which one it was is
            # more useful than a count that does not add up.
            print(f"  could not discard: {item.directory}/{item.key}")
            continue
        gone += 1
    print(f"\nDiscarded {gone} of {len(doomed)} files, freeing {format_size(freed)}.")
    print("Their records remain, so none of them will be fetched again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
