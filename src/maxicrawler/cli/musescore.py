"""Rendering of a MuseScore worklist for the terminal.

Pure functions, so the wording can be tested without a database or a folder.
Same split every renderer here follows: the command asks a service, this turns
the answer into lines.

**Every printed line is ASCII**, which is the unwritten rule the rest of this
package already keeps: the prose in these docstrings uses whatever punctuation
reads best, and nothing that reaches a terminal does. Windows consoles default
to cp1252, where an arrow or an em dash is not a mangled character but an
unhandled ``UnicodeEncodeError`` — the command does not print oddly, it stops.
"""

from maxicrawler.app.musescore import Match, Today


def render_today(today: Today, matches: tuple[Match, ...], *, folder: str) -> str:
    """Return what there is to do today, and what has come back.

    The allowance goes first because it is the fact that decides whether the
    rest is worth reading. A day with nothing left says so in one line rather
    than printing a list somebody cannot act on.
    """
    budget = today.budget
    lines = [
        f"Allowance for {budget.day}: {budget.spent} of {budget.limit} taken, "
        f"{budget.remaining} left",
        f"Backlog: {today.waiting} waiting",
    ]
    if today.returned:
        lines.append(f"Returned from an earlier day: {today.returned}")
    lines.extend(("", "To do:"))
    if budget.exhausted:
        lines.append("nothing: today's allowance is spent")
    elif not today.offered:
        lines.append("nothing: the backlog is empty")
    else:
        lines.extend(f"  {request.label}\n    {request.score_url}" for request in today.offered)
    lines.extend(("", f"What arrived in {folder}:"))
    if not matches:
        lines.append("nothing new")
    else:
        lines.extend(_arrival_line(match) for match in matches)
    return "\n".join(lines)


def _arrival_line(match: Match) -> str:
    """Return one line for a file found in the download folder.

    An unplaced file keeps its reason. "MaxiCrawler ignored my file" is a worse
    thing to read than "two lines could be this pdf".
    """
    name = match.arrival.path.name
    if match.request is None:
        return f"  {name}: {match.reason}"
    return f"  {name} -> {match.request.label}\n    keep it with: --keep {match.request.request_id}"


def render_added(added: int, *, formats: tuple[str, ...]) -> str:
    """Return what queueing a batch of addresses came to."""
    if not added:
        return "Nothing new: every score in that was already on the list."
    renderings = ", ".join(formats)
    return f"Added {added} lines to the backlog ({renderings})."
