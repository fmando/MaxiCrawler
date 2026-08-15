"""Small copies of stored images, so a page of tiles is a page and not a download.

**A thumbnail is only ever a cache.** It can be deleted in full at any moment
and the library is unchanged; it is never an entry's account of itself, and it
never lives inside ``library/``. A library directory holds what was downloaded
and what the download said about itself, and a picture this module made is
neither. Everything below follows from that: the cache is addressed by content
rather than by name, nothing here writes to a record, and losing the whole
directory costs one run of the maker.

Why it is needed at all is a matter of pixels rather than bytes. A tile that
loads the stored file shows a photograph at the size it was downloaded, and a
browser holds a decoded image at four bytes a pixel whatever it was compressed
to. Measured on a real library of six thousand photographs, the sixty largest
images that a byte limit still lets through come to **3.3 GB of bitmap on one
page**. The same sixty at 240 pixels come to fourteen megabytes.

Pillow is optional, and absent it everything here answers "no thumbnail" — which
the caller already has to handle for a file that is not an image. It is the
first dependency in this project that decodes untrusted bytes, which is why
:data:`MAX_PIXELS` is set explicitly rather than left to the library's own
default, and why nothing here is called from a request path.
"""

import hashlib
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image, ImageOps, UnidentifiedImageError

    AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by the environment, not a test
    AVAILABLE = False

DEFAULT_SIZE = 240
"""How long the longest edge of a thumbnail is, in pixels.

What a tile of the grid actually displays. Sixty of these decode to about
fourteen megabytes, against gigabytes for the originals.
"""

QUALITY = 80
"""WebP quality. Above this the files grow without the tiles looking different."""

SUFFIX = ".webp"
"""What the cached files are.

One format for everything, including images that came in with transparency:
WebP keeps an alpha channel, which JPEG would flatten, and is markedly smaller
than PNG for the photographs that make up most of a crawl's take.
"""

MAX_PIXELS = 120_000_000
"""The largest image this will decode, in pixels.

Stated here rather than left to the decoder's own default, because the files
here came off the open web: a few kilobytes of image header can claim dimensions
whose bitmap would be tens of gigabytes, and the first thing a decoder does with
that claim is allocate it. A hundred and twenty megapixels is comfortably above
the largest thing a camera produces — the largest in one real library measured
82 — and far below what a machine cannot survive.

Pillow carries a ceiling of its own, near ninety megapixels, and it is set to
this one on import so there is a single number rather than two that disagree.
Its own is the earlier of the two checks: it fires while the header is being
read, before this module has seen the file at all, which is why the error it
raises is caught below.
"""

if AVAILABLE:
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS


@dataclass(frozen=True, slots=True)
class ThumbnailCache:
    """A directory of small copies, addressed by what they are copies of.

    *root* is outside the library, always. The caller decides where — beside the
    database is the intended spot — and this refuses nothing, because a cache
    that policed its own location would be the second place that knows the
    layout.
    """

    root: Path
    size: int = DEFAULT_SIZE

    def path_for(self, key: str) -> Path:
        """Return where the thumbnail for *key* belongs, whether or not it exists.

        Nested one level by the first two characters of the key. Ten thousand
        files in a single directory is slow to list on every filesystem and
        painful on some, and the fix is two characters wide.
        """
        return self.root / key[:2] / f"{key}{SUFFIX}"

    def get(self, key: str) -> Path | None:
        """Return the cached thumbnail for *key*, or ``None`` when there is none."""
        path = self.path_for(key)
        try:
            return path if path.is_file() else None
        except OSError:
            return None

    def make(self, source: Path, key: str) -> Path | None:
        """Produce the thumbnail for *source* under *key* and return its path.

        ``None`` for every way of not having one, and they are all ordinary: no
        Pillow installed, a file that is not an image, an image whose dimensions
        are refused, a decoder that gave up part way through a truncated
        download. A caller has one thing to do about all of them, which is show
        the symbol it would have shown anyway.

        An existing thumbnail is returned untouched. The key changes when the
        file does, so a stale one cannot be returned; what would be pointless is
        making the same picture twice.
        """
        if not AVAILABLE:
            return None
        existing = self.get(key)
        if existing is not None:
            return existing
        destination = self.path_for(key)
        try:
            with Image.open(source) as image:
                if image.width * image.height > MAX_PIXELS:
                    return None
                # Lets a JPEG decoder skip straight to a smaller size rather
                # than build the full bitmap and shrink it. On a library of
                # photographs this is most of the run time and nearly all of the
                # peak memory.
                image.draft("RGB", (self.size, self.size))
                upright = ImageOps.exif_transpose(image)
                small = upright if upright is not None else image
                small.thumbnail((self.size, self.size))
                if small.mode not in ("RGB", "RGBA"):
                    small = small.convert("RGBA" if "A" in small.mode else "RGB")
                destination.parent.mkdir(parents=True, exist_ok=True)
                small.save(destination, "WEBP", quality=QUALITY, method=4)
        except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError):
            # A truncated download, a file whose suffix lied, a header claiming
            # more pixels than anything should decode, a decoder that refused.
            # None of it is this module's business to report on: the doctor is
            # what says a file is damaged.
            self._discard(destination)
            return None
        return destination

    def _discard(self, path: Path) -> None:
        """Remove a half-written thumbnail, ignoring whether there was one."""
        with suppress(OSError):
            path.unlink(missing_ok=True)

    def every(self) -> list[Path]:
        """Return every thumbnail in the cache."""
        if not self.root.is_dir():
            return []
        return sorted(self.root.rglob(f"*{SUFFIX}"))

    def forget(self, keys: set[str]) -> int:
        """Delete every cached thumbnail whose key is not in *keys*, and count them.

        How the cache stays finite. A re-download changes an entry's key, and
        the picture made from what used to be there stops being reachable
        without stopping taking up room.
        """
        removed = 0
        for path in self.every():
            if path.stem in keys:
                continue
            try:
                path.unlink()
            except OSError:
                continue
            removed += 1
        return removed


def key_for(
    *,
    directory: str,
    key: str,
    checksum: str | None = None,
    stamp: tuple[int, int] | None = None,
) -> str:
    """Return the cache key for one stored file.

    By checksum where the record carries one, which makes the cache
    content-addressed: two entries holding the same picture share one
    thumbnail, and a re-download that fetched identical bytes finds the
    thumbnail already made.

    Where there is no checksum, the entry's own name and the file's modification
    time and length stand in — the same pair the listing cache trusts a row on
    (ADR-037), and for the same reason: a timestamp has a resolution, and a file
    rewritten inside it is caught by its length changing.

    Hashed rather than assembled, so the result is a fixed-length name that is
    safe as a path component whatever the entry was called.
    """
    if checksum:
        material = f"sha256:{checksum}"
    else:
        modified, size = stamp if stamp is not None else (0, 0)
        material = f"entry:{directory}/{key}:{modified}:{size}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def cache_beside(database_path: Path) -> Path:
    """Return where the thumbnails for the library that database indexes belong.

    Beside the database rather than inside the library, which is the rule this
    module exists to keep: everything in ``library/`` is what was downloaded or
    what the download said about itself. It sits next to the other derived thing
    on the disk, and the two can be deleted together without a thought.
    """
    return database_path.with_name(database_path.name + ".thumbs")
