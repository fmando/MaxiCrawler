"""Compare what the library says about itself against what is on the disk.

The filesystem is the authority (ADR-010) and every record is a claim about it.
The entry page checks that claim for the one file somebody is looking at; nobody
checks it for the shelf. So this walks the directories — not the index, which
would only tell us what it was told — and reports where the two have come apart:
a record pointing at a file that is gone, a file no record mentions, a size or a
checksum that no longer matches, an interrupted download nobody swept up.

**It repairs only where the intention is already on record.** With ``--apply``
it clears staging leftovers, which ADR-012 says are worthless the moment a
transfer stops, and it finishes discards whose file is somehow still there. It
never removes a file no record mentions and never rewrites a record whose
payload has gone: both would be a *new* decision about what should exist, and
that is not a maintenance script's to make. Those are reported, with
``--urls`` printing what to queue again.

Usage::

    python scripts/check_library.py --config settings.toml
    python scripts/check_library.py --config settings.toml --checksums
    python scripts/check_library.py --config settings.toml --apply
"""

import hashlib
import sys
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shelf import parser_for, settings_from  # noqa: E402

from maxicrawler.app import LibraryService  # noqa: E402
from maxicrawler.domain import ReviewVerdict  # noqa: E402
from maxicrawler.library import (  # noqa: E402
    CONTENT_DIRECTORY,
    Library,
    LibraryEntry,
    LibraryRecordError,
    ResourceRecord,
)
from maxicrawler.utils import format_size  # noqa: E402

EXAMPLES = 10
"""How many entries are named under each heading before the rest is a count.

A library with nine hundred of one fault has one fault, and printing it nine
hundred times buries the other five. ``--all`` prints everything.
"""

READ_SIZE = 1024 * 1024


@dataclass(frozen=True)
class Finding:
    """One thing that is not as the library says it is."""

    fault: str
    """Which heading it is reported under."""

    entry: str
    """``provider/key``, which is what a URL and a listing row both carry."""

    detail: str = ""

    url: str | None = None
    """Where the payload came from, for the ones worth fetching again."""


UNREADABLE = "A metadata document could not be read"
MISSING = "A record points at a file that is not there"
WRONG_CHECKSUM = "A file does not match its recorded checksum"
WRONG_SIZE = "A file is not the size its record gives"
NO_RECORD = "An entry directory holds no metadata document"
UNCLAIMED = "A stored file no record mentions"
UNFINISHED_DISCARD = "A discarded entry still has its file"
LEFTOVER = "An interrupted download left something behind"

ORDER = (
    UNREADABLE,
    MISSING,
    WRONG_CHECKSUM,
    WRONG_SIZE,
    NO_RECORD,
    UNCLAIMED,
    UNFINISHED_DISCARD,
    LEFTOVER,
)
"""The headings, worst first.

Grouped by what they cost: something unreadable or gone, then something whose
contents are not what was recorded, then bookkeeping. Printing them in the order
the directory walk happened to find them would put the wasted megabyte above the
lost file.
"""

REPAIRABLE = (LEFTOVER, UNFINISHED_DISCARD)
"""The two faults ``--apply`` acts on.

Both are an intention already written down — a transfer that stopped, a discard
that was asked for — being carried out. Everything else would be this script
deciding something new about what should exist.
"""


def _reason(error: Exception) -> str:
    """Return why something failed, without the path it happened to.

    The library states both in one sentence; the path is already the entry this
    is printed beside, and repeating it pushes the reason off the line.
    """
    return str(error).partition(": ")[0]


def check(entry: LibraryEntry, *, checksums: bool) -> Iterator[Finding]:
    """Yield everything wrong with one entry."""
    name = f"{entry.provider}/{entry.key}"
    try:
        record = entry.read()
    except LibraryRecordError as error:
        yield Finding(UNREADABLE, name, _reason(error))
        return
    if record is None:
        if any(entry.path.rglob("*")):
            yield Finding(NO_RECORD, name, "directory is not empty")
        return

    yield from _check_staging(entry, name)
    yield from _check_payload(entry, record, name, checksums=checksums)
    yield from _check_unclaimed(entry, record, name)


def _check_staging(entry: LibraryEntry, name: str) -> Iterator[Finding]:
    """Yield a finding for anything left under the staging directory."""
    staged = [path for path in entry.staging_directory.glob("*") if path.is_file()]
    if staged:
        wasted = sum(path.stat().st_size for path in staged)
        yield Finding(LEFTOVER, name, f"{len(staged)} file(s), {format_size(wasted)}")


def _check_payload(
    entry: LibraryEntry, record: ResourceRecord, name: str, *, checksums: bool
) -> Iterator[Finding]:
    """Yield findings about the file the record claims."""
    if record.content is None:
        return
    path = entry.path / record.content.path
    discarded = record.review is not None and record.review.verdict is ReviewVerdict.DISCARDED
    try:
        stat = path.stat()
    except OSError:
        if record.is_complete and not discarded:
            yield Finding(MISSING, name, record.content.filename, url=record.source_url)
        return

    if discarded:
        yield Finding(UNFINISHED_DISCARD, name, record.content.filename)
        return
    if stat.st_size != record.content.size:
        claimed = format_size(record.content.size)
        yield Finding(
            WRONG_SIZE, name, f"record says {claimed}, disk says {format_size(stat.st_size)}"
        )
        # A file of the wrong size will not match its checksum either, and
        # saying so twice is noise rather than a second fault.
        return
    if checksums:
        yield from _check_checksum(path, record, name)


def _check_checksum(path: Path, record: ResourceRecord, name: str) -> Iterator[Finding]:
    """Yield a finding when the file no longer hashes to what was recorded."""
    if record.content is None:
        return
    recorded = record.content.checksum("sha256")
    if recorded is None:
        return
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(READ_SIZE):
                digest.update(chunk)
    except OSError as error:
        yield Finding(UNREADABLE, name, _reason(error))
        return
    if digest.hexdigest() != recorded:
        yield Finding(WRONG_CHECKSUM, name, record.content.filename, url=record.source_url)


def _check_unclaimed(entry: LibraryEntry, record: ResourceRecord, name: str) -> Iterator[Finding]:
    """Yield findings for stored files the record does not account for.

    A re-download under a different filename leaves the old payload sitting
    there, counted by nothing and served by nothing.
    """
    claimed = record.content.path if record.content is not None else None
    for path in sorted(entry.content_directory.glob("*")):
        if not path.is_file():
            continue
        relative = f"{CONTENT_DIRECTORY}/{path.name}"
        if relative != claimed:
            yield Finding(UNCLAIMED, name, f"{path.name}, {format_size(path.stat().st_size)}")


def repair(shelf: LibraryService, library: Library, finding: Finding) -> bool:
    """Carry out the one repair *finding* calls for, returning whether it worked."""
    provider, _, key = finding.entry.partition("/")
    entry = library.entry_at(provider, key)
    if entry is None:
        return False
    if finding.fault == LEFTOVER:
        for path in entry.staging_directory.glob("*"):
            try:
                path.unlink()
            except OSError:
                return False
        return True
    if finding.fault == UNFINISHED_DISCARD:
        # Through the service, so the record is brought up to date in the same
        # step the file goes (ADR-041) rather than by a second opinion here.
        return shelf.discard(provider, key) is not None
    return False


def main() -> int:
    """Print the report, and repair what was already decided when asked to."""
    parser = parser_for(__doc__.splitlines()[0])
    parser.add_argument(
        "--checksums",
        action="store_true",
        help="also read every file to verify its recorded digest; slow",
    )
    parser.add_argument(
        "--urls",
        action="store_true",
        help="print the source URLs of files that are gone, to queue again",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help=f"name every affected entry rather than the first {EXAMPLES}",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="clear staging leftovers and finish discards; nothing else is touched",
    )
    args = parser.parse_args()
    settings = settings_from(args.config)
    library = Library(settings.library_path)

    entries = 0
    findings: list[Finding] = []
    for entry in library.entries():
        entries += 1
        findings.extend(check(entry, checksums=args.checksums))

    print(f"Library:  {settings.library_path}")
    print(f"Checked:  {entries:,} entries" + (", with checksums" if args.checksums else ""))
    if not findings:
        print("\nNothing to report: every record matches what is on the disk.")
        return 0

    by_fault: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        by_fault[finding.fault].append(finding)

    for fault in ORDER:
        found = by_fault.get(fault)
        if not found:
            continue
        print(f"\n{fault}  ({len(found):,})")
        shown = found if args.all else found[:EXAMPLES]
        for finding in shown:
            print(f"  {finding.entry}  {finding.detail}")
        if len(found) > len(shown):
            print(f"  ... and {len(found) - len(shown):,} more (--all to list them)")

    if args.urls:
        lost = [finding.url for finding in findings if finding.url]
        if lost:
            print(f"\nSource URLs of the {len(lost):,} that could be fetched again")
            for url in lost:
                print(f"  {url}")

    repairable = [finding for finding in findings if finding.fault in REPAIRABLE]
    if not repairable:
        print("\nNone of this is repairable from here; the rest is a decision to make.")
        return 0

    if not args.apply:
        print(
            f"\n{len(repairable):,} of these can be put right from here"
            " (leftovers cleared, discards finished)."
            "\nNothing was written. Run again with --apply to do it."
        )
        return 0

    shelf = LibraryService(settings)
    repaired = sum(1 for finding in repairable if repair(shelf, library, finding))
    print(f"\nPut right: {repaired:,} of {len(repairable):,}.")
    if repaired < len(repairable):
        print("The rest could not be, most likely a file another program is holding open.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
