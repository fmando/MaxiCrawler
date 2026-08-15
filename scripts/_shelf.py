"""What every script here needs before it can do its own job.

Finding the library, opening it, and reading all of it. Kept in one place from
the second script onwards rather than the fifth, because the interesting part of
these tools is the pass they make over the shelf, and none of it is the four
lines that get them there.

Underscored so the test that walks this directory skips it: it is not a script,
and running it would do nothing.
"""

import argparse
import sys
from dataclasses import replace
from pathlib import Path

# Run from a checkout without installing it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from maxicrawler.app import LibraryItem, LibraryQuery, LibraryService  # noqa: E402
from maxicrawler.config import Settings  # noqa: E402
from maxicrawler.domain import ReviewVerdict  # noqa: E402

PAGE_SIZE = 500
"""How many entries are asked for at a time. Large enough that a library of ten
thousand is twenty questions, small enough that none of them is the whole thing.
"""


def parser_for(description: str) -> argparse.ArgumentParser:
    """Return a parser carrying the argument every script here takes.

    ``--config`` is how a script finds the library, and it is the same file the
    server reads — so a tool run against the wrong shelf is a mistake somebody
    has to make deliberately.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="settings file to read the library path from; the defaults otherwise",
    )
    return parser


def settings_from(config: Path | None) -> Settings:
    """Return the settings at *config*, or the defaults when none was named."""
    return Settings.from_toml(config) if config else Settings()


def every_item(shelf: LibraryService, *, discarded: bool = False) -> list[LibraryItem]:
    """Return every entry in the library, page by page.

    Collected in full before anything is done with it rather than acted on while
    paging: a script that discards as it walks changes what page two holds, and
    a listing that moves under a loop is how half of a set gets skipped.

    A plain listing leaves out what was discarded, and deliberately so — the
    verdict means "out of my way". A script that *describes* the shelf has to
    ask for those separately, hence *discarded*; one that acts on files wants
    the default, since there is nothing left of them to act on.
    """
    items = _all_pages(shelf, LibraryQuery(per_page=PAGE_SIZE))
    if discarded:
        items.extend(
            _all_pages(shelf, LibraryQuery(per_page=PAGE_SIZE, verdict=ReviewVerdict.DISCARDED))
        )
    return items


def holds_a_file(item: LibraryItem) -> bool:
    """Return whether *item* has a payload still on disk.

    Not the same question as :attr:`LibraryItem.is_stored`, which asks whether
    the *record* claims a finished payload — and a discarded entry goes on
    claiming one. That is the point of a headstone: it keeps saying what the
    file was, its size and its checksum, so the entry stays searchable and is
    never fetched again (ADR-041). For counting what is on the shelf, though, it
    is an entry with nothing behind it, and adding its bytes to a total would
    report space that is already free.
    """
    return item.is_stored and item.verdict is not ReviewVerdict.DISCARDED


def _all_pages(shelf: LibraryService, query: LibraryQuery) -> list[LibraryItem]:
    """Return every row *query* matches, asking one page at a time."""
    items: list[LibraryItem] = []
    page_number = 1
    while True:
        page = shelf.browse(replace(query, page=page_number))
        items.extend(page.items)
        if page_number >= page.pages:
            return items
        page_number += 1
