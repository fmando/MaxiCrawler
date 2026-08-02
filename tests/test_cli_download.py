"""Tests for the download command and its renderers.

The command is exercised against a stub provider swapped in at the composition
root, so the whole path — argument parsing, configuration, library placement,
reporting, exit codes — is covered without a socket.
"""

import json
from pathlib import Path

import pytest
from doubles import StubProvider, make_ref
from typer.testing import CliRunner

from maxicrawler.cli import app
from maxicrawler.cli.downloads import (
    EXIT_DOWNLOADS_COMPLETE,
    EXIT_DOWNLOADS_INCOMPLETE,
    render_plan,
    render_report,
)
from maxicrawler.domain import (
    Checksum,
    DownloadStatus,
    ProviderCapability,
    ResourceKind,
)
from maxicrawler.downloader import (
    DownloadJob,
    DownloadOutcome,
    DownloadPlan,
    DownloadReport,
    UnresolvedSource,
)
from maxicrawler.library import METADATA_FILENAME
from maxicrawler.providers import ProviderRegistry, ProviderTransportError

runner = CliRunner()
FILE_URL = "https://mega.nz/file/AaBbCcDd#0123456789abcdefghijkl"
PAYLOAD = b"stub payload"
DOWNLOADS = frozenset({ProviderCapability.INSPECT, ProviderCapability.DOWNLOAD})


@pytest.fixture
def stub_provider(monkeypatch: pytest.MonkeyPatch) -> StubProvider:
    """Replace the built-in provider set with a stub that answers for Mega."""
    provider = StubProvider(
        "mega",
        url_prefix="https://mega.nz/",
        capabilities=DOWNLOADS,
        payload=PAYLOAD,
    )
    monkeypatch.setattr(
        "maxicrawler.cli.create_default_provider_registry",
        lambda **_: ProviderRegistry([provider]),
    )
    return provider


def stored_entries(library: Path) -> list[Path]:
    """Return every entry directory the library holds."""
    return sorted(path for path in library.rglob(METADATA_FILENAME))


def test_a_link_is_downloaded_into_the_default_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_provider: StubProvider
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["download", FILE_URL, "--no-progress"])

    assert result.exit_code == EXIT_DOWNLOADS_COMPLETE
    assert "Downloaded: 1" in result.stdout
    assert "Library: library" in result.stdout
    assert len(stored_entries(tmp_path / "library")) == 1


def test_the_output_option_chooses_the_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_provider: StubProvider
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app, ["download", FILE_URL, "--output", str(tmp_path / "elsewhere"), "--no-progress"]
    )

    assert result.exit_code == EXIT_DOWNLOADS_COMPLETE
    assert len(stored_entries(tmp_path / "elsewhere")) == 1
    assert not (tmp_path / "library").exists()


def test_the_configured_library_is_honoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_provider: StubProvider
) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "custom.toml"
    config.write_text('[maxicrawler]\nlibrary_path = "archive"\n', encoding="utf-8")

    result = runner.invoke(app, ["download", FILE_URL, "--config", str(config), "--no-progress"])

    assert result.exit_code == EXIT_DOWNLOADS_COMPLETE
    assert len(stored_entries(tmp_path / "archive")) == 1


def test_the_stored_payload_and_metadata_sit_side_by_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_provider: StubProvider
) -> None:
    monkeypatch.chdir(tmp_path)

    runner.invoke(app, ["download", FILE_URL, "--no-progress"])

    metadata = stored_entries(tmp_path / "library")[0]
    document = json.loads(metadata.read_text(encoding="utf-8"))
    assert document["provider"] == "mega"
    assert document["status"] == DownloadStatus.COMPLETED.value
    assert (metadata.parent / document["content"]["path"]).read_bytes() == PAYLOAD


def test_a_document_of_links_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_provider: StubProvider
) -> None:
    monkeypatch.chdir(tmp_path)
    links = tmp_path / "links.txt"
    links.write_text(f"{FILE_URL}\n", encoding="utf-8")

    result = runner.invoke(app, ["download", "links.txt", "--no-progress"])

    assert result.exit_code == EXIT_DOWNLOADS_COMPLETE
    assert "Downloaded: 1" in result.stdout


def test_a_directory_of_documents_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_provider: StubProvider
) -> None:
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / "shares.md").write_text(f"- [share]({FILE_URL})\n", encoding="utf-8")

    result = runner.invoke(app, ["download", "notes", "--no-progress"])

    assert result.exit_code == EXIT_DOWNLOADS_COMPLETE
    assert "Downloaded: 1" in result.stdout


def test_a_second_run_skips_what_is_already_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_provider: StubProvider
) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["download", FILE_URL, "--no-progress"])

    result = runner.invoke(app, ["download", FILE_URL, "--no-progress"])

    assert result.exit_code == EXIT_DOWNLOADS_COMPLETE
    assert "Skipped: 1" in result.stdout
    assert "Downloaded: 0" in result.stdout
    assert len(stub_provider.downloaded) == 1


def test_a_dry_run_transfers_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_provider: StubProvider
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["download", FILE_URL, "--dry-run"])

    assert result.exit_code == EXIT_DOWNLOADS_COMPLETE
    assert "Planned downloads: 1" in result.stdout
    assert stub_provider.downloaded == []
    assert not (tmp_path / "library").exists()


def test_a_failed_transfer_is_reported_with_a_non_zero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    provider = StubProvider(
        "mega",
        url_prefix="https://mega.nz/",
        capabilities=DOWNLOADS,
        failure=ProviderTransportError("connection reset"),
    )
    monkeypatch.setattr(
        "maxicrawler.cli.create_default_provider_registry",
        lambda **_: ProviderRegistry([provider]),
    )

    result = runner.invoke(app, ["download", FILE_URL, "--no-progress"])

    assert result.exit_code == EXIT_DOWNLOADS_INCOMPLETE
    assert "Failed: 1" in result.stdout
    assert "connection reset" in result.stdout


def test_an_unhandled_link_is_reported_with_a_non_zero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_provider: StubProvider
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["download", "https://example.test/file.iso", "--no-progress"])

    assert result.exit_code == EXIT_DOWNLOADS_INCOMPLETE
    assert "Not downloaded:" in result.stdout
    assert "no provider can handle this link" in result.stdout


def test_a_source_that_is_neither_a_link_nor_a_path_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_provider: StubProvider
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["download", "nowhere.txt", "--no-progress"])

    assert result.exit_code != 0
    assert "neither an HTTP(S) URL nor an existing path" in result.output


def test_the_key_never_reaches_the_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_provider: StubProvider
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["download", FILE_URL, "--no-progress"])

    assert "0123456789abcdefghijkl" not in result.output


def make_job(name: str, size: int | None) -> DownloadJob:
    """Return a job for the renderer tests."""
    return DownloadJob(ref=make_ref(kind=ResourceKind.FILE), name=name, size=size)


def test_a_report_states_the_four_counts_and_the_library() -> None:
    job = make_job("ubuntu.iso", 5_800_000_000)
    report = DownloadReport(
        plan=DownloadPlan(jobs=(job,)),
        outcomes=(
            DownloadOutcome(
                job=job,
                status=DownloadStatus.COMPLETED,
                bytes_written=5_800_000_000,
                checksums=(Checksum("sha256", "ab" * 32),),
            ),
        ),
        library_root=Path("library"),
    )

    assert render_report(report) == (
        "Downloaded: 1\nSkipped: 0\nFailed: 0\nStored: 5.8 GB\nLibrary: library"
    )


def test_a_report_lists_failures_and_unresolved_sources() -> None:
    job = make_job("ubuntu.iso", None)
    report = DownloadReport(
        plan=DownloadPlan(
            jobs=(job,),
            unresolved=(UnresolvedSource("https://example.test/a", "no provider"),),
        ),
        outcomes=(
            DownloadOutcome(job=job, status=DownloadStatus.FAILED, reason="connection reset"),
        ),
    )

    rendered = render_report(report)

    assert "Failures:\n  ubuntu.iso: connection reset" in rendered
    assert "Not downloaded:\n  https://example.test/a: no provider" in rendered


def test_a_plan_lists_what_would_be_downloaded() -> None:
    plan = DownloadPlan(jobs=(make_job("ubuntu.iso", 1000), make_job("notes.txt", 24)))

    rendered = render_plan(plan, Path("library"))

    assert rendered.startswith("Planned downloads: 2\nTotal size: 1.0 KB\nLibrary: library")
    assert "  ubuntu.iso  1.0 KB" in rendered
    assert "  notes.txt   24 B" in rendered


def test_a_plan_reports_an_unknown_total() -> None:
    plan = DownloadPlan(jobs=(make_job("ubuntu.iso", None),))

    assert "Total size: unknown" in render_plan(plan)


def test_an_empty_plan_renders_without_a_listing() -> None:
    assert render_plan(DownloadPlan()) == "Planned downloads: 0\nTotal size: 0 B"
