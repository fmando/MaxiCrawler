"""Turning numbers into what a person reads.

One byte size, one implementation. The terminal and the web interface word most
things differently — a report writes "stopped at the page limit" where a page
shows a badge — but "1.3 MB" is not wording, it is arithmetic, and two copies of
it would eventually disagree about what a kilobyte is.

Decimal units, because that is what a provider advertises: a share Mega calls
1.3 MB should not be reported as 1.2 MiB by us. The binary units the
configuration page uses are a different question and stay where they are.

:func:`parse_size` is the same arithmetic read backwards, and lives here for the
same reason: a field where somebody types "10 MB" has to mean what the page
beside it prints, and two implementations would eventually disagree about the
one thing this module exists to keep single.
"""

import re

SIZE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")
"""Decimal units, so a size matches what the provider advertises."""

SIZE_MULTIPLIERS = {unit: 1000**power for power, unit in enumerate(SIZE_UNITS)}
"""What each unit is worth, read off the same list :func:`format_size` prints."""

_SIZE_PATTERN = re.compile(
    r"^(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>[a-z]*)$", re.IGNORECASE | re.ASCII
)
"""A number, optional space, optional unit. Anything else is not a size."""

UNKNOWN_SIZE = "unknown"
"""What an absent size is called; never ``0 B``, which is a finding."""


def format_size(size: int | None) -> str:
    """Return *size* in bytes as a short human-readable string.

    ``None`` is unknown rather than zero. A provider that states no length and
    a payload of no bytes are different things, and a reader has to be able to
    tell them apart.
    """
    if size is None:
        return UNKNOWN_SIZE
    if size < 1000:
        return f"{size} B"
    value = float(size)
    for unit in SIZE_UNITS[1:]:
        value /= 1000
        if value < 1000:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} {SIZE_UNITS[-1]}"


def parse_size(text: str | None) -> int | None:
    """Return the byte count *text* names, or ``None`` when it names none.

    Accepts what somebody actually types into a box beside a listing that reads
    "1.3 MB": a bare number of bytes, a number with a unit, with or without a
    space, in either case, and with a comma for a decimal point because half the
    world writes it that way.

    ``None`` for empty text, for a unit this module does not print, and for
    anything that is not a number — every one of them for the same reason the
    sort order is read leniently: the value arrives in a query string, and a
    listing with one filter fewer is a better answer to a typo than a refusal.

    A bare number is **bytes**, not the unit of whatever box it was typed in.
    Guessing megabytes there would make "500" mean half a gigabyte to somebody
    who meant half a kilobyte, and the two are eight hundred thousand apart.
    """
    if text is None:
        return None
    match = _SIZE_PATTERN.match(text.strip())
    if match is None:
        return None
    unit = match["unit"].upper() or "B"
    multiplier = SIZE_MULTIPLIERS.get(unit)
    if multiplier is None:
        return None
    return int(float(match["number"].replace(",", ".")) * multiplier)
