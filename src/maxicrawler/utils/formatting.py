"""Turning numbers into what a person reads.

One byte size, one implementation. The terminal and the web interface word most
things differently — a report writes "stopped at the page limit" where a page
shows a badge — but "1.3 MB" is not wording, it is arithmetic, and two copies of
it would eventually disagree about what a kilobyte is.

Decimal units, because that is what a provider advertises: a share Mega calls
1.3 MB should not be reported as 1.2 MiB by us. The binary units the
configuration page uses are a different question and stay where they are.
"""

SIZE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")
"""Decimal units, so a size matches what the provider advertises."""

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
