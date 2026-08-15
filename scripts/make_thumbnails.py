"""Make the small copies a tile shows, for every stored image that has none.

The tile view asks for a thumbnail and shows the stored file when there is
none. Nothing makes them on demand, deliberately: a page of sixty tiles would
be sixty image decodes inside one request, and whoever opened a fresh library
first would pay for all of them. This is the run that pays instead — once,
watched, and then again in seconds for whatever has arrived since.

Measured at about a tenth of a second per photograph, so a library of six
thousand images is a few minutes the first time.

**It also sweeps.** With ``--apply`` a thumbnail no entry can reach any more is
deleted: a re-download changes what a picture is filed under, and the copy made
from what used to be there stops being reachable without stopping taking up
room. Nothing is swept when no images were found at all, so a run pointed at
the wrong settings file empties nothing.

Losing this whole directory costs one more run and nothing else. That is what
makes it a cache (ADR-044).

Usage::

    python scripts/make_thumbnails.py --config settings.toml
    python scripts/make_thumbnails.py --config settings.toml --apply
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shelf import every_item, holds_a_file, parser_for, settings_from  # noqa: E402

from maxicrawler.app import LibraryItem, LibraryService  # noqa: E402
from maxicrawler.app.thumbnails import AVAILABLE, ThumbnailCache, cache_beside  # noqa: E402
from maxicrawler.utils import format_size  # noqa: E402

REPORT_EVERY = 100
"""How often the run says where it has got to.

Often enough that a wait of minutes is visibly a wait rather than a hang, rarely
enough that the output is still readable afterwards.
"""

MISSING_EXTRA = """Refusing: Pillow is not installed, so no thumbnail can be made.

    uv sync --extra thumbnails

Without it the library works exactly as before: a tile shows the stored image
where it is small enough, and a symbol where it is not."""


def remaining(seconds: float) -> str:
    """Return how much longer this will take, in a unit worth reading.

    "0 min left" is what a run of a few seconds reported before, which tells
    somebody watching nothing at all.
    """
    if seconds < 90:
        return f"{seconds:.0f} s"
    return f"{seconds / 60:.0f} min"


def weigh(cache: ThumbnailCache) -> tuple[int, int]:
    """Return how many thumbnails the cache holds and what they occupy."""
    files = cache.every()
    total = 0
    for path in files:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return len(files), total


def main() -> int:
    """Make what is missing, or say what is missing."""
    parser = parser_for(__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually make them, and sweep up the ones nothing can reach",
    )
    args = parser.parse_args()
    settings = settings_from(args.config)

    shelf = LibraryService(settings)
    cache = ThumbnailCache(cache_beside(settings.database_path))

    print(f"Library:  {settings.library_path}")
    print(f"Cache:    {cache.root}")
    print("Reading every entry...", flush=True)

    wanted: dict[str, LibraryItem] = {}
    for item in every_item(shelf):
        if not holds_a_file(item):
            continue
        key = shelf.thumbnail_key(item)
        if key is not None:
            wanted[key] = item
    missing = [(key, item) for key, item in wanted.items() if cache.get(key) is None]
    held, occupied = weigh(cache)
    stale = max(0, held - (len(wanted) - len(missing)))

    print(f"Images:   {len(wanted):,} hold a file this could depict")
    print(f"Cached:   {held:,} thumbnails, {format_size(occupied)}")
    print(f"Missing:  {len(missing):,}")
    if stale:
        print(f"Stale:    {stale:,} that no entry can reach any more")

    if not missing and not stale:
        print("\nNothing to do.")
        return 0

    if not args.apply:
        print("\nNothing was written. Run again with --apply to make them.")
        return 0

    if not AVAILABLE:
        print(f"\n{MISSING_EXTRA}")
        return 1

    made = 0
    refused = 0
    started = time.perf_counter()
    for index, (key, item) in enumerate(missing, start=1):
        if item.path is not None and cache.make(item.path, key) is not None:
            made += 1
        else:
            # Not a fault to report on: a file whose suffix lied, a truncated
            # download, an image too large to decode. The doctor is what says a
            # file is damaged; this one just has nothing to show for it.
            refused += 1
        if index % REPORT_EVERY == 0:
            elapsed = time.perf_counter() - started
            left = (len(missing) - index) * elapsed / index
            print(f"  {index:,} of {len(missing):,}, about {remaining(left)} left", flush=True)

    took = time.perf_counter() - started
    print(f"\nMade {made:,} in {took:.0f} s.")
    if refused:
        print(f"{refused:,} could not be read as images and were left without one.")

    if wanted:
        swept = cache.forget(set(wanted))
        if swept:
            print(f"Swept {swept:,} that no entry could reach any more.")
    held, occupied = weigh(cache)
    print(f"Cache now holds {held:,} thumbnails, {format_size(occupied)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
