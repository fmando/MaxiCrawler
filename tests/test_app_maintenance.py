"""Tests for the description of the maintenance scripts.

Two kinds of test, and the first kind is the reason this module exists. A
description of a program that lives somewhere else goes stale silently: the
script grows a flag, or is renamed, or is deleted, and the page goes on saying
what used to be true. So the claims are checked against the scripts themselves —
the directory decides which runs exist, and each script's own ``--help`` decides
whether it writes.

The second kind is about the command that is printed. It has to be pasteable as
it stands, which means absolute paths and quoting that survives a space.
"""

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from maxicrawler.app.maintenance import RUNS, MaintenanceRun, Toolbox, scripts_directory, toolbox

REPOSITORY = Path(__file__).resolve().parent.parent
SCRIPTS = REPOSITORY / "scripts"


def every_script() -> list[str]:
    """Return the file name of every script in the directory.

    The same rule the scripts' own tests use: underscored files are not scripts,
    they are what the scripts share.
    """
    return sorted(path.name for path in SCRIPTS.glob("*.py") if not path.name.startswith("_"))


def run_named(script: str) -> MaintenanceRun:
    """Return the description of *script*."""
    return next(run for run in RUNS if run.script == script)


def test_the_directory_is_found_in_a_checkout() -> None:
    """Running from source, the scripts are where the walk upwards says."""
    assert scripts_directory() == SCRIPTS


def test_the_directory_is_only_ours_if_it_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """A directory called `scripts` without our marker in it is somebody else's.

    The path is computed from the package's own location, and in an installed
    environment that walk lands among directories nobody here created.
    """
    monkeypatch.setattr(
        "maxicrawler.app.maintenance.MARKER", "a_file_that_is_not_there.py", raising=True
    )
    assert scripts_directory() is None


def test_every_script_is_described() -> None:
    """A script added to the directory is a script this page has to name."""
    assert sorted(run.script for run in RUNS) == every_script()


def test_no_description_names_a_missing_script() -> None:
    """And the other direction: nothing is offered that cannot be run."""
    for run in RUNS:
        assert (SCRIPTS / run.script).is_file()


@pytest.mark.parametrize("run", RUNS, ids=lambda run: run.script)
def test_writes_matches_what_the_script_offers(run: MaintenanceRun) -> None:
    """`writes` is checked against the program rather than trusted.

    A script that gains the ability to write gains ``--apply`` with it, and the
    page would go on calling it read-only. Asking the script settles it.
    """
    help_text = subprocess.run(
        [sys.executable, str(SCRIPTS / run.script), "--help"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert ("--apply" in help_text) is run.writes


def test_every_extra_is_one_that_can_be_installed() -> None:
    """A run naming an extra names one the project actually declares."""
    packaging = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    declared = packaging["project"]["optional-dependencies"]
    for run in RUNS:
        if run.extra is not None:
            assert run.extra in declared


def test_the_survey_is_the_one_that_reads() -> None:
    """The list is ordered from the harmless to the sharp, and stays that way."""
    assert RUNS[0].script == "survey_library.py"
    assert RUNS[-1].script == "start_over.py"


def test_the_command_names_the_interpreter_the_script_and_the_config(tmp_path: Path) -> None:
    """Absolute throughout, so where it is pasted from does not matter."""
    config = tmp_path / "settings.toml"
    box = Toolbox(scripts=SCRIPTS, python=Path(sys.executable), config=config)

    command = box.command(run_named("survey_library.py"))

    assert command is not None
    assert sys.executable in command
    assert str(SCRIPTS / "survey_library.py") in command
    assert f"--config {config}" in command or f'--config "{config}"' in command


def test_the_command_leaves_out_a_config_that_was_never_given() -> None:
    """A server on the defaults prints a command that reads the defaults too."""
    box = Toolbox(scripts=SCRIPTS, python=Path(sys.executable), config=None)

    command = box.command(run_named("survey_library.py"))

    assert command is not None
    assert "--config" not in command


def test_apply_is_added_where_it_was_asked_for(tmp_path: Path) -> None:
    """The second line on a card, and the only difference between the two."""
    box = Toolbox(scripts=SCRIPTS, python=Path(sys.executable), config=tmp_path / "settings.toml")
    run = run_named("prune_small_payloads.py")

    plain = box.command(run)
    applied = box.command(run, apply=True)

    assert plain is not None
    assert applied is not None
    assert "--apply" not in plain
    # The two lines a card shows, and the flag is the whole difference between
    # them -- which is what makes reading the first one before running the
    # second one worth anything.
    assert applied == f"{plain} --apply"


def test_apply_is_refused_for_a_run_that_does_not_write() -> None:
    """Printing a command the script would reject helps nobody."""
    box = Toolbox(scripts=SCRIPTS, python=Path(sys.executable), config=None)

    with pytest.raises(ValueError, match="--apply"):
        box.command(run_named("survey_library.py"), apply=True)


def test_there_is_no_command_without_a_directory() -> None:
    """Installed from a wheel there are no scripts, and the page says so."""
    box = Toolbox(scripts=None, python=Path(sys.executable), config=None)

    assert box.command(run_named("survey_library.py")) is None


def test_a_path_with_a_space_survives(tmp_path: Path) -> None:
    """The paths on a real machine have spaces in them, and this one does.

    Checked per platform because that is what the quoting does: the line is for
    the shell of the machine that printed it, so there is no single right answer
    to assert against.
    """
    config = tmp_path / "a directory" / "settings.toml"
    box = Toolbox(scripts=SCRIPTS, python=Path(sys.executable), config=config)

    command = box.command(run_named("survey_library.py"))

    assert command is not None
    quoted = f'"{config}"' if os.name == "nt" else f"'{config}'"
    assert quoted in command


def test_the_toolbox_reports_this_process(tmp_path: Path) -> None:
    """What it knows about itself, which is where the command comes from."""
    config = tmp_path / "settings.toml"

    box = toolbox(config)

    assert box.python == Path(sys.executable)
    assert box.config == config
    assert box.scripts == SCRIPTS
