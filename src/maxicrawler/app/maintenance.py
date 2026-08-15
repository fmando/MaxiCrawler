"""What can be run against a library from a shell, and what to type to do it.

The scripts in ``scripts/`` are not part of the application. Nothing in ``src/``
imports them, and this module does not either — what it holds is a *description*
of them: what each one is for, whether it writes, and the command that would run
it on this machine. A client shows that; nothing here starts a process.

The interface has no authentication (ADR-025) and is run with ``--allow-remote``
on the machine it serves from, so a button that ran one of these would be a
button anybody who can reach the port may press — and one of them moves a whole
library aside. Printing the command is what a page can honestly offer: whoever
pastes it is at a shell on that machine already, which is the permission check.

The command is built from what this process knows about itself rather than
guessed: the interpreter that is running, the settings file it was started with,
and the directory beside the package. That is why it can be pasted as it stands,
from any working directory, instead of assuming somebody is in the checkout with
``uv`` on their path.
"""

import os
import shlex

# Imported for `list2cmdline`, which is the quoting rule for one of the two
# shells below. Nothing here starts a process, and that is the point of it.
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

MARKER = "_shelf.py"
"""What proves a directory is the one holding *our* scripts.

The same shape of check `start_over.py` makes before it moves a library: a path
computed from another path is a guess until something in it says so. An
installed wheel has no scripts directory, and the walk upwards from the package
would otherwise land somewhere in the environment and find whatever is there.
"""


@dataclass(frozen=True, slots=True)
class MaintenanceRun:
    """One script in ``scripts/``, described for somebody deciding to run it."""

    script: str
    """Its file name, which is also how it is found on disk."""

    title: str
    """What it does, in a few words."""

    summary: str
    """Why it exists, for a reader who has not read its docstring."""

    writes: bool
    """Whether it changes anything, and therefore offers ``--apply``.

    Not a label but a claim about the program: the tests run each script's
    ``--help`` and fail when the two disagree, so this cannot quietly go stale.
    """

    extra: str | None = None
    """An optional dependency the run needs, when it needs one."""

    caution: str | None = None
    """What is worth knowing before running it, where there is something."""


RUNS: tuple[MaintenanceRun, ...] = (
    MaintenanceRun(
        script="survey_library.py",
        title="What is on the shelf",
        summary=(
            "Describes the whole library: how much of it there is, of what "
            "kinds, how big the files are, and how many pixels the images "
            "carry. The two settings that were given starting values rather "
            "than measured ones — preview_inline_bytes and whether thumbnails "
            "are worth making — are decided from this."
        ),
        writes=False,
    ),
    MaintenanceRun(
        script="check_library.py",
        title="Every record, against the disk",
        summary=(
            "Walks the directories and reports where the records and the files "
            "have come apart: a payload that is gone, a file no record "
            "mentions, a size or a checksum that no longer matches, an "
            "interrupted download nobody swept up. Add --checksums to read "
            "every byte, and --urls to print what to queue again."
        ),
        writes=True,
        caution=(
            "It repairs only what was already decided: staging leftovers and "
            "discards whose file is still there. Everything else it reports, "
            "because removing it would be a new decision about your library."
        ),
    ),
    MaintenanceRun(
        script="make_thumbnails.py",
        title="The small copies a tile shows",
        summary=(
            "Makes a thumbnail for every stored image that has none, and "
            "deletes the ones no entry can reach any more. Nothing makes them "
            "inside a request, so this is the run that pays for them — a few "
            "minutes the first time and seconds after. Worth running when a "
            "crawl has brought new images in."
        ),
        writes=True,
        extra="thumbnails",
    ),
    MaintenanceRun(
        script="reindex_library.py",
        title="The listing cache, read again",
        summary=(
            "Drops every cached row for this library so the next listing "
            "rebuilds it from the documents themselves. For a library that has "
            "moved between machines, or a crash that left the two disagreeing. "
            "It drops rows; it never removes a database."
        ),
        writes=True,
    ),
    MaintenanceRun(
        script="prune_small_payloads.py",
        title="Payloads too small to keep",
        summary=(
            "Discards stored files below a size, the way the interface would. "
            "For the sprites, icons and thumbnails an image directory answered "
            "with before min_download_size existed."
        ),
        writes=True,
        caution=(
            "The files go. The records stay as headstones, so the entries are "
            "still searchable, say plainly that they were thrown away, and are "
            "not fetched again until that is taken back."
        ),
    ),
    MaintenanceRun(
        script="start_over.py",
        title="An empty shelf, with the old one kept",
        summary=(
            "Moves the library and its database aside under a timestamp and "
            "leaves an empty library in their place. It renames rather than "
            "deletes, prints the commands to change your mind, and refuses if "
            "the path is not one of ours."
        ),
        writes=True,
        caution=(
            "Stop the server first. The check that the database opens for "
            "writing is a hint, not a guarantee — on Linux a file can be "
            "renamed while something is still writing into it."
        ),
    ),
)
"""Every script in the directory, in the order they get sharper.

Written out rather than read off the disk: a script's own docstring is written
for whoever is reading the source, and this is written for whoever is deciding
whether to run it. What keeps the two in step is a test that walks the
directory — a script added without a line here fails it, and so does a line here
naming a script that is not there.
"""


@dataclass(frozen=True, slots=True)
class Toolbox:
    """Where the scripts are on this machine, and what would run them."""

    scripts: Path | None
    """The directory, or ``None`` where there is none — an installed wheel."""

    python: Path
    """The interpreter running this process, which is the one to run them with."""

    config: Path | None
    """The settings file this process was started with, if it was given one."""

    def command(self, run: MaintenanceRun, *, apply: bool = False) -> str | None:
        """Return the line that runs *run* here, or ``None`` without a directory.

        Absolute throughout, so it works from whatever directory it is pasted
        into. ``--config`` is passed only when this process was given one; a
        server running on the defaults would otherwise print a path to a file
        that does not exist.

        Raises:
            ValueError: *apply* was asked for a run that does not write. The
                flag would be refused by the script itself, and printing a
                command that cannot work is worse than failing here.
        """
        if apply and not run.writes:
            raise ValueError(f"{run.script} does not write, so it takes no --apply")
        if self.scripts is None:
            return None
        parts = [str(self.python), str(self.scripts / run.script)]
        if self.config is not None:
            parts += ["--config", str(self.config)]
        if apply:
            parts.append("--apply")
        return _shell(parts)


def scripts_directory() -> Path | None:
    """Return the directory holding the maintenance scripts, if it is there.

    Beside the package rather than inside it, which is what makes them not part
    of what is shipped — and what makes this return ``None`` for an installation
    from a wheel, where they were never copied.
    """
    candidate = Path(__file__).resolve().parents[3] / "scripts"
    return candidate if (candidate / MARKER).is_file() else None


def toolbox(config: Path | None = None) -> Toolbox:
    """Return what this process knows about running the scripts.

    *config* is the settings file it was started with, which a client already
    holds — it is passed in rather than looked up, for the same reason the
    settings page is handed the path it read from: this process is the authority
    on what it was told, not on what a default would have been.
    """
    return Toolbox(scripts=scripts_directory(), python=Path(sys.executable), config=config)


def _shell(parts: Sequence[str]) -> str:
    """Return *parts* as one line for the shell of the machine this runs on.

    Two dialects, because the line is meant to be pasted on the machine that
    printed it: Windows quoting where that is what a shell there expects, POSIX
    quoting everywhere else. Picking one would be right on one machine and
    quietly wrong on the other, and a path with a space in it is not exotic.
    """
    if os.name == "nt":
        return subprocess.list2cmdline(parts)
    return " ".join(shlex.quote(part) for part in parts)
