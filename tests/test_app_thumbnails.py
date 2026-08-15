"""Tests for the cache of small copies.

Real images, made here and decoded back, because what is under test is a
decoder's behaviour and a picture drawn by hand would only prove that a stub
returns what it was given. Nothing in this file touches a library: the cache
does not know what an entry is, and that is the property that keeps it a cache.
"""

from pathlib import Path

import pytest

from maxicrawler.app import thumbnails
from maxicrawler.app.thumbnails import (
    DEFAULT_SIZE,
    SUFFIX,
    ThumbnailCache,
    cache_beside,
    key_for,
)

pillow = pytest.importorskip("PIL", reason="the thumbnails extra is not installed")
Image = pillow.Image


def draw(path: Path, width: int, height: int, *, fmt: str = "PNG", colour: str = "red") -> Path:
    """Write a real image of that size and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, height), colour).save(path, fmt)
    return path


def opened(path: Path) -> tuple[int, int]:
    """Return the dimensions of the image at *path*."""
    with Image.open(path) as image:
        return image.size


# --- what a thumbnail is ------------------------------------------------------


def test_a_thumbnail_is_made_at_the_asked_for_edge(tmp_path: Path) -> None:
    cache = ThumbnailCache(tmp_path / "thumbs")
    source = draw(tmp_path / "big.png", 4000, 3000)

    made = cache.make(source, "abcd1234")

    assert made is not None
    assert max(opened(made)) == DEFAULT_SIZE


def test_the_shape_of_the_picture_is_kept(tmp_path: Path) -> None:
    """A tile crops with CSS; squashing here would be a lie in the file itself."""
    cache = ThumbnailCache(tmp_path / "thumbs")
    source = draw(tmp_path / "wide.png", 4000, 1000)

    made = cache.make(source, "abcd1234")

    assert made is not None
    width, height = opened(made)
    assert (width, height) == (DEFAULT_SIZE, DEFAULT_SIZE // 4)


def test_a_small_image_is_not_made_bigger(tmp_path: Path) -> None:
    cache = ThumbnailCache(tmp_path / "thumbs")
    source = draw(tmp_path / "tiny.png", 40, 30)

    made = cache.make(source, "abcd1234")

    assert made is not None
    assert opened(made) == (40, 30)


def test_the_cache_nests_by_the_first_two_characters(tmp_path: Path) -> None:
    """Ten thousand files in one directory is slow to list everywhere."""
    cache = ThumbnailCache(tmp_path / "thumbs")

    path = cache.path_for("abcd1234")

    assert path == tmp_path / "thumbs" / "ab" / f"abcd1234{SUFFIX}"


def test_making_one_twice_does_the_work_once(tmp_path: Path) -> None:
    cache = ThumbnailCache(tmp_path / "thumbs")
    source = draw(tmp_path / "big.png", 2000, 2000)

    first = cache.make(source, "abcd1234")
    assert first is not None
    stamped = first.stat().st_mtime_ns

    again = cache.make(source, "abcd1234")

    assert again == first
    assert again.stat().st_mtime_ns == stamped


def test_an_orientation_recorded_in_the_file_is_applied(tmp_path: Path) -> None:
    """A photograph taken sideways is stored sideways and shown upright.

    Cameras write the rotation into the metadata rather than into the pixels, so
    a thumbnail that ignores it comes out lying on its side — on a library of
    photographs, for a good share of them.
    """
    source = tmp_path / "sideways.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (400, 200), "blue")
    exif = image.getexif()
    exif[274] = 6  # rotate 90 degrees clockwise
    image.save(source, "JPEG", exif=exif)
    cache = ThumbnailCache(tmp_path / "thumbs")

    made = cache.make(source, "abcd1234")

    assert made is not None
    width, height = opened(made)
    assert height > width, "the wide image should have come out tall"


# --- what is not a thumbnail --------------------------------------------------


def test_a_file_that_is_not_an_image_makes_nothing(tmp_path: Path) -> None:
    cache = ThumbnailCache(tmp_path / "thumbs")
    source = tmp_path / "notes.txt"
    source.write_text("not a picture", encoding="utf-8")

    assert cache.make(source, "abcd1234") is None
    assert not cache.path_for("abcd1234").exists()


def test_a_truncated_image_makes_nothing_and_leaves_nothing(tmp_path: Path) -> None:
    """An interrupted download is an ordinary thing to find in a library."""
    cache = ThumbnailCache(tmp_path / "thumbs")
    whole = draw(tmp_path / "whole.jpg", 800, 600, fmt="JPEG").read_bytes()
    source = tmp_path / "half.jpg"
    source.write_bytes(whole[: len(whole) // 3])

    made = cache.make(source, "abcd1234")

    assert made is None
    assert not cache.path_for("abcd1234").exists(), "no half-written thumbnail is left behind"


def test_a_missing_file_makes_nothing(tmp_path: Path) -> None:
    cache = ThumbnailCache(tmp_path / "thumbs")

    assert cache.make(tmp_path / "was-never-here.png", "abcd1234") is None


def test_an_image_claiming_too_many_pixels_is_refused(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The header is a claim, and decoding it is what allocates the memory.

    A few kilobytes off the open web can announce dimensions whose bitmap is
    tens of gigabytes. The ceiling is checked against what the header says,
    before anything is decoded.
    """
    cache = ThumbnailCache(tmp_path / "thumbs")
    source = draw(tmp_path / "ordinary.png", 2000, 2000)
    monkeypatch.setattr(thumbnails, "MAX_PIXELS", 1000)

    assert cache.make(source, "abcd1234") is None


def test_the_decoders_own_ceiling_is_the_same_number(tmp_path: Path) -> None:
    """One ceiling, not two that disagree.

    Pillow's own default sits below the one this module states, and fires first
    — while the header is being read. Left alone, the number in this file would
    be decoration.
    """
    assert Image.MAX_IMAGE_PIXELS == thumbnails.MAX_PIXELS


def test_a_bomb_the_decoder_catches_first_is_still_just_no_thumbnail(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """The error Pillow raises on the way in has to be caught, not propagated."""
    cache = ThumbnailCache(tmp_path / "thumbs")
    source = draw(tmp_path / "ordinary.png", 2000, 2000)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)

    assert cache.make(source, "abcd1234") is None
    assert not cache.path_for("abcd1234").exists()


def test_without_pillow_there_are_simply_no_thumbnails(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The extra is optional, and its absence is an ordinary answer."""
    cache = ThumbnailCache(tmp_path / "thumbs")
    source = draw(tmp_path / "big.png", 800, 600)
    monkeypatch.setattr(thumbnails, "AVAILABLE", False)

    assert cache.make(source, "abcd1234") is None


# --- staying finite -----------------------------------------------------------


def test_a_thumbnail_nobody_can_reach_any_more_is_swept_up(tmp_path: Path) -> None:
    """A re-download changes an entry's key; the old picture stops being reachable."""
    cache = ThumbnailCache(tmp_path / "thumbs")
    draw(tmp_path / "one.png", 400, 400)
    cache.make(tmp_path / "one.png", "aaaa1111")
    cache.make(tmp_path / "one.png", "bbbb2222")

    removed = cache.forget({"aaaa1111"})

    assert removed == 1
    assert cache.get("aaaa1111") is not None
    assert cache.get("bbbb2222") is None


def test_an_empty_cache_has_nothing_to_sweep(tmp_path: Path) -> None:
    cache = ThumbnailCache(tmp_path / "thumbs")

    assert cache.every() == []
    assert cache.forget(set()) == 0


# --- addressing ---------------------------------------------------------------


def test_two_entries_holding_the_same_bytes_share_one_thumbnail(tmp_path: Path) -> None:
    """The checksum is already recorded, and it is what the picture is of."""
    first = key_for(directory="mega", key="one", checksum="a" * 64)
    second = key_for(directory="direct", key="two", checksum="a" * 64)

    assert first == second


def test_a_different_file_gets_a_different_key(tmp_path: Path) -> None:
    assert key_for(directory="mega", key="one", checksum="a" * 64) != key_for(
        directory="mega", key="one", checksum="b" * 64
    )


def test_without_a_checksum_the_key_follows_the_file_on_disk(tmp_path: Path) -> None:
    """The same pair the listing cache trusts a row on: modification time and length."""
    stamped = key_for(directory="mega", key="one", stamp=(1234, 500))

    assert stamped != key_for(directory="mega", key="one", stamp=(1234, 501))
    assert stamped != key_for(directory="mega", key="one", stamp=(9999, 500))
    assert stamped == key_for(directory="mega", key="one", stamp=(1234, 500))


def test_a_key_is_usable_as_a_file_name(tmp_path: Path) -> None:
    """Whatever the entry was called, including things a path cannot hold."""
    key = key_for(directory="mega", key="../../etc/passwd", stamp=(1, 2))

    assert key.isalnum()
    assert len(key) == 32


# --- what it is taking up -----------------------------------------------------


def test_a_cache_that_was_never_made_takes_up_nothing(tmp_path: Path) -> None:
    """The state a fresh installation is in, and no reason to fail in it."""
    usage = ThumbnailCache(tmp_path / "never-made").usage()

    assert usage.count == 0
    assert usage.total_bytes == 0
    assert usage.root == tmp_path / "never-made"


def test_the_cache_counts_what_it_holds(tmp_path: Path) -> None:
    """How much room the small copies come to, which is the only reason to ask."""
    cache = ThumbnailCache(tmp_path / "thumbs")
    cache.make(draw(tmp_path / "one.png", 900, 600), "aaaa")
    cache.make(draw(tmp_path / "two.png", 900, 600, colour="blue"), "bbbb")

    usage = cache.usage()

    assert usage.count == 2
    assert usage.total_bytes == sum(path.stat().st_size for path in cache.every())
    assert usage.total_bytes > 0


def test_the_cache_lives_beside_the_database_and_never_in_the_library() -> None:
    """The one rule the module exists to keep."""
    beside = cache_beside(Path("/srv/maxicrawler/maxicrawler.db"))

    assert beside == Path("/srv/maxicrawler/maxicrawler.db.thumbs")
    assert "library" not in beside.parts
