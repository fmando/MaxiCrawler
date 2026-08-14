"""Describe what is actually in a library: how much, of what, and how big.

The interface answers this a page at a time. Some questions are only askable of
the whole shelf, and two of them decide settings that were guessed at:

**Is ``preview_inline_bytes`` set anywhere near right?** A tile shows the file
itself below that size and a symbol above it. Which of those two the library
mostly gets is not knowable from the default of one megabyte — it is knowable
from the pile.

**Would thumbnails be worth generating?** That turns on pixels, not bytes: a
300 KB photograph at 6000x4000 costs 96 MB as a bitmap in a browser, and sixty
of those is not a page. So the image headers are read for their dimensions —
just the headers, a few hundred bytes each, no decoding and no dependency.

**This script never writes anything**, so it takes no ``--apply``. It opens
image files to read their first bytes and nothing else.

Usage::

    python scripts/survey_library.py --config settings.toml
"""

import sys
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shelf import every_item, holds_a_file, parser_for, settings_from  # noqa: E402

from maxicrawler.app import LibraryItem, LibraryService  # noqa: E402
from maxicrawler.app.viewing import MediaKind  # noqa: E402
from maxicrawler.domain import ReviewVerdict  # noqa: E402
from maxicrawler.utils import format_size  # noqa: E402

SIZE_CLASSES: tuple[int, ...] = (
    100_000,
    1_000_000,
    10_000_000,
    100_000_000,
    1_000_000_000,
)
"""The boundaries the size histogram is cut at, in bytes.

Decimal, and the first two are the two settings this is meant to inform:
``min_download_size`` and ``preview_inline_bytes`` default to exactly these.
"""

PIXEL_CLASSES: tuple[int, ...] = (1_000_000, 4_000_000, 12_000_000, 30_000_000)
"""Boundaries for the dimension histogram, in pixels.

One megapixel is about what a tile can use; twelve is a phone photograph; above
thirty a single image is a bitmap larger than most tabs should hold.
"""

HEADER_BYTES = 65_536
"""How much of an image is read to find its dimensions.

Enough for a JPEG whose frame header sits behind a large embedded thumbnail, and
bounded so that a file claiming to be an image cannot cost more than this.
"""


def image_size(path: Path) -> tuple[int, int] | None:
    """Return the pixel dimensions of the image at *path*, or ``None``.

    PNG, GIF and JPEG are read here, which is what a crawl mostly brings home.
    Anything else — WebP, AVIF, TIFF — is reported as unread rather than
    guessed at: the histogram says how many it could not measure, and a number
    that admits its own gap is worth more than one that quietly fills it.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(HEADER_BYTES)
    except OSError:
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
        return (
            int.from_bytes(head[16:20], "big"),
            int.from_bytes(head[20:24], "big"),
        )
    if head[:6] in (b"GIF87a", b"GIF89a") and len(head) >= 10:
        return (
            int.from_bytes(head[6:8], "little"),
            int.from_bytes(head[8:10], "little"),
        )
    if head[:2] == b"\xff\xd8":
        return _jpeg_size(head)
    return None


def _jpeg_size(head: bytes) -> tuple[int, int] | None:
    """Return the dimensions in a JPEG's frame header, walking its segments.

    A JPEG is a chain of segments, each announcing its own length, and the
    dimensions live in whichever start-of-frame segment comes first. Walking the
    chain is the only way to it; the offset is not fixed, because what precedes
    it — colour profiles, EXIF, an embedded preview — varies in size.
    """
    at = 2
    while at + 9 < len(head):
        if head[at] != 0xFF:
            return None
        marker = head[at + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            at += 2
            continue
        length = int.from_bytes(head[at + 2 : at + 4], "big")
        if length < 2:
            return None
        # Start of frame, in any of its flavours. The four that are not frames
        # (0xC4 Huffman tables, 0xC8 an extension, 0xCC arithmetic coding) sit
        # in the middle of that range and have to be stepped over.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return (
                int.from_bytes(head[at + 7 : at + 9], "big"),
                int.from_bytes(head[at + 5 : at + 7], "big"),
            )
        at += 2 + length
    return None


def tally(items: Iterable[LibraryItem], attribute: str) -> list[tuple[str, int, int]]:
    """Return (label, count, bytes) grouped by one attribute, largest group first."""
    counts: Counter[str] = Counter()
    bytes_of: Counter[str] = Counter()
    for item in items:
        label = str(getattr(item, attribute))
        counts[label] += 1
        bytes_of[label] += item.size or 0
    return [(label, count, bytes_of[label]) for label, count in counts.most_common()]


def format_pixels(pixels: int) -> str:
    """Return a pixel count in megapixels.

    Its own unit, and not ``format_size`` with a different name on it: a picture
    measured in "MB" when the number counts pixels is the sort of label somebody
    acts on before noticing.
    """
    return f"{pixels / 1_000_000:.0f} MP"


def histogram(
    values: Sequence[int],
    boundaries: Sequence[int],
    unit: Callable[[int], str] = format_size,
) -> list[tuple[str, int]]:
    """Return how many of *values* fall into each band cut at *boundaries*.

    Empty bands at either end are dropped and empty bands *between* two occupied
    ones are kept. A histogram reads as a run along a scale, and silently
    closing a gap in the middle of it says there is none.
    """
    labels = [f"under {unit(boundaries[0])}"]
    labels += [
        f"{unit(low)} to {unit(high)}"
        for low, high in zip(boundaries, boundaries[1:], strict=False)
    ]
    labels.append(f"over {unit(boundaries[-1])}")
    counted = [0] * len(labels)
    for value in values:
        band = sum(1 for boundary in boundaries if value >= boundary)
        counted[band] += 1
    occupied = [index for index, count in enumerate(counted) if count]
    if not occupied:
        return []
    return [(labels[index], counted[index]) for index in range(occupied[0], occupied[-1] + 1)]


def print_table(title: str, rows: Iterable[tuple[str, int, int]]) -> None:
    """Print one grouping, or say that it is empty."""
    rows = list(rows)
    print(f"\n{title}")
    if not rows:
        print("  (none)")
        return
    for label, count, total in rows:
        print(f"  {label:<14} {count:>7,}  {format_size(total):>10}")


def print_histogram(title: str, rows: Iterable[tuple[str, int]], of: int) -> None:
    """Print a histogram with a share beside each band."""
    rows = list(rows)
    print(f"\n{title}")
    if not rows:
        print("  (none)")
        return
    for label, count in rows:
        share = f"{100 * count / of:.0f}%" if of else "-"
        print(f"  {label:<24} {count:>7,}  {share:>5}")


def report_images(items: Sequence[LibraryItem], *, inline_limit: int, measure: bool) -> None:
    """Print what the images are, in the terms the tile settings are written in.

    *items* are the entries that hold a file, so nothing here has to ask again.
    """
    images = [item for item in items if item.kind is MediaKind.IMAGE]
    if not images:
        print("\nImages\n  (none)")
        return

    print(f"\nImages and the tile limit  (preview_inline_bytes = {format_size(inline_limit)})")
    fits = [item.size is not None and item.size <= inline_limit for item in images]
    inline = sum(fits)
    heavy = len(images) - inline
    heavy_bytes = sum(item.size or 0 for item, shown in zip(images, fits, strict=True) if not shown)
    print(f"  shown as themselves      {inline:>7,}  {100 * inline / len(images):>4.0f}%")
    print(
        f"  shown as a symbol        {heavy:>7,}"
        f"  {100 * heavy / len(images):>4.0f}%  {format_size(heavy_bytes)}"
    )

    if not measure:
        return

    measured: list[tuple[int, LibraryItem]] = []
    unread = 0
    for item in images:
        dimensions = image_size(item.path) if item.path is not None else None
        if dimensions is None:
            unread += 1
            continue
        width, height = dimensions
        measured.append((width * height, item))
    if not measured:
        print(f"\nImage dimensions\n  none of {len(images):,} could be read")
        return

    title = f"Image dimensions  ({len(measured):,} read, {unread:,} in formats not read here)"
    print_histogram(
        title,
        histogram([pixels for pixels, _ in measured], PIXEL_CLASSES, format_pixels),
        len(measured),
    )
    pixels, largest = max(measured, key=lambda pair: pair[0])
    print(f"  largest: {pixels / 1_000_000:.1f} MP, {format_size(largest.size)}  {largest.name}")


def main() -> int:
    """Print the survey."""
    parser = parser_for(__doc__.splitlines()[0])
    parser.add_argument(
        "--skip-dimensions",
        action="store_true",
        help="do not open image files to read their pixel sizes",
    )
    args = parser.parse_args()
    settings = settings_from(args.config)

    shelf = LibraryService(settings)
    # Discarded entries included: this is a description of the shelf, and the
    # headstones are part of what is on it.
    items = every_item(shelf, discarded=True)
    stored = [item for item in items if holds_a_file(item)]
    total = sum(item.size or 0 for item in stored)
    unsized = sum(1 for item in stored if item.size is None)
    headstones = sum(1 for item in items if item.verdict is ReviewVerdict.DISCARDED)

    print(f"Library:  {settings.library_path}")
    print(f"Entries:  {len(items):,}, of which {len(stored):,} hold a file, {format_size(total)}")
    if headstones:
        print(f"Discarded:{headstones:>6,} more are records of files thrown away")
    if unsized:
        print(f"Unsized:  {unsized:,} of those record no size and are left out of the totals")
    if not items:
        return 0

    print_table("By type", tally(stored, "kind"))
    print_table("By provider", tally(items, "provider"))
    print_table("By verdict", tally(items, "verdict"))

    sizes = [item.size for item in stored if item.size is not None]
    print_histogram("By size", histogram(sizes, SIZE_CLASSES), len(sizes))
    below = sum(1 for size in sizes if size < settings.min_download_size)
    if settings.min_download_size > 0:
        print(
            f"\n  {below:,} stored files are under min_download_size"
            f" ({format_size(settings.min_download_size)}) and predate it"
            " or came in another way."
        )

    report_images(
        stored,
        inline_limit=settings.preview_inline_bytes,
        measure=not args.skip_dimensions,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
