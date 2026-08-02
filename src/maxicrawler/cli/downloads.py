"""Rendering of download plans and reports for the terminal.

Both renderers are pure, so the wording can be tested without transferring
anything. Neither can emit a credential: a job carries a
:class:`~maxicrawler.domain.providers.ResourceRef` whose URL already had its
fragment removed, and the secret it belongs to is never read here.

What is printed here goes to standard output. Progress bars go to standard
error, so a report stays usable when it is redirected into a file.
"""

from pathlib import Path

from maxicrawler.cli.inspection import format_size
from maxicrawler.downloader import DownloadPlan, DownloadReport, UnresolvedSource

EXIT_DOWNLOADS_COMPLETE = 0
"""Every requested resource is in the library."""

EXIT_DOWNLOADS_INCOMPLETE = 4
"""Something the user asked for did not happen; the report says what."""


def exit_code_for(report: DownloadReport) -> int:
    """Return the process exit code that reports *report*.

    A skipped download counts as success — the resource is present — while an
    unresolved source does not, because the user asked for something that never
    happened.
    """
    return EXIT_DOWNLOADS_COMPLETE if report.succeeded else EXIT_DOWNLOADS_INCOMPLETE


def render_report(report: DownloadReport) -> str:
    """Return the terminal report for a finished run."""
    lines = [
        f"Downloaded: {len(report.completed)}",
        f"Skipped: {len(report.skipped)}",
        f"Failed: {len(report.failed)}",
        f"Stored: {format_size(report.bytes_written)}",
    ]
    if report.library_root is not None:
        lines.append(f"Library: {_display(report.library_root)}")
    if report.failed:
        lines.extend(("", "Failures:"))
        lines.extend(
            f"  {outcome.label}: {outcome.reason or 'no reason given'}" for outcome in report.failed
        )
    lines.extend(_unresolved_lines(report.unresolved))
    return "\n".join(lines)


def render_plan(plan: DownloadPlan, library_root: Path | None = None) -> str:
    """Return the terminal report for a plan that will not be executed."""
    lines = [f"Planned downloads: {len(plan.jobs)}"]
    total = plan.total_size
    lines.append(f"Total size: {format_size(total) if total is not None else 'unknown'}")
    if library_root is not None:
        lines.append(f"Library: {_display(library_root)}")
    if plan.jobs:
        width = max(len(job.label) for job in plan.jobs)
        lines.extend(("", "Would download:"))
        lines.extend(
            f"  {job.label.ljust(width)}  {format_size(job.size)}".rstrip() for job in plan.jobs
        )
    lines.extend(_unresolved_lines(plan.unresolved))
    return "\n".join(lines)


def _unresolved_lines(unresolved: tuple[UnresolvedSource, ...]) -> list[str]:
    """Return the lines listing what never became a download."""
    if not unresolved:
        return []
    return ["", "Not downloaded:", *(f"  {item.url}: {item.reason}" for item in unresolved)]


def _display(path: Path) -> str:
    """Return *path* in a form that reads the same on every platform."""
    return path.as_posix()
