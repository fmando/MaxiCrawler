"""Tests for progress reporting."""

import io

from doubles import RecordingProgressReporter, make_ref
from rich.console import Console

from maxicrawler.domain import DownloadStatus
from maxicrawler.downloader import (
    DownloadJob,
    DownloadOutcome,
    NullProgressReporter,
    ProgressReporter,
    RichProgressReporter,
)

JOB = DownloadJob(ref=make_ref(), name="ubuntu.iso", size=1024)


def outcome(status: DownloadStatus, *, written: int = 1024) -> DownloadOutcome:
    """Return an outcome for the shared job."""
    return DownloadOutcome(job=JOB, status=status, bytes_written=written)


def make_reporter() -> tuple[RichProgressReporter, io.StringIO]:
    """Return a Rich reporter writing into a buffer instead of a terminal."""
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=True, width=100)
    return RichProgressReporter(console), buffer


def test_every_reporter_satisfies_the_runtime_protocol() -> None:
    assert isinstance(NullProgressReporter(), ProgressReporter)
    assert isinstance(RecordingProgressReporter(), ProgressReporter)
    assert isinstance(make_reporter()[0], ProgressReporter)


def test_the_null_reporter_says_nothing() -> None:
    reporter = NullProgressReporter()

    reporter.begin()
    reporter.started(JOB, 1024)
    reporter.advanced(JOB, 512)
    reporter.finished(JOB, outcome(DownloadStatus.COMPLETED))
    reporter.end()


def test_a_transfer_is_rendered_with_its_name() -> None:
    reporter, buffer = make_reporter()

    reporter.begin()
    reporter.started(JOB, 1024)
    reporter.advanced(JOB, 512)
    reporter.finished(JOB, outcome(DownloadStatus.COMPLETED))
    reporter.end()

    assert "ubuntu.iso" in buffer.getvalue()


def test_a_failure_is_named_on_its_bar() -> None:
    reporter, buffer = make_reporter()

    reporter.begin()
    reporter.started(JOB, 1024)
    reporter.finished(JOB, outcome(DownloadStatus.FAILED, written=0))
    reporter.end()

    assert "failed" in buffer.getvalue()


def test_a_transfer_of_unknown_size_is_still_rendered() -> None:
    reporter, buffer = make_reporter()

    reporter.begin()
    reporter.started(JOB, None)
    reporter.advanced(JOB, 4096)
    reporter.finished(JOB, outcome(DownloadStatus.COMPLETED, written=4096))
    reporter.end()

    assert "ubuntu.iso" in buffer.getvalue()


def test_a_job_that_was_never_started_is_ignored() -> None:
    reporter, _ = make_reporter()

    reporter.begin()
    reporter.advanced(JOB, 10)
    reporter.finished(JOB, outcome(DownloadStatus.SKIPPED, written=0))
    reporter.end()


def test_the_reporter_can_be_used_as_a_context_manager() -> None:
    reporter, buffer = make_reporter()

    with reporter:
        reporter.started(JOB, 1024)
        reporter.finished(JOB, outcome(DownloadStatus.COMPLETED))

    assert "ubuntu.iso" in buffer.getvalue()


def test_progress_never_reaches_standard_output_by_default(
    capsys: object,
) -> None:
    reporter = RichProgressReporter()

    reporter.begin()
    reporter.started(JOB, 1024)
    reporter.finished(JOB, outcome(DownloadStatus.COMPLETED))
    reporter.end()

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert captured.out == ""
