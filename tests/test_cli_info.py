"""Tests for the ``info`` command."""

import json
from pathlib import Path
from typing import Any

import pytest
from mega_fixtures import (
    CHILD_FILE_HANDLE,
    FILE_AES_KEY,
    SHARE_KEY,
    RecordingTransport,
    encode_base64,
    file_answer,
    file_url,
    folder_answer,
    folder_url,
    pack_file_key,
)
from typer.testing import CliRunner
from web_server import Site, serve

from maxicrawler import cli
from maxicrawler.cli import app
from maxicrawler.cli.inspection import EXIT_UNAVAILABLE, EXIT_UNDETERMINED

runner = CliRunner()
FILE_URL = file_url(key=FILE_AES_KEY)
FOLDER_URL = folder_url()
FILE_KEY = encode_base64(pack_file_key(FILE_AES_KEY))
FOLDER_KEY = encode_base64(SHARE_KEY)


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> RecordingTransport:
    """Replace the command's HTTP transport and isolate the configuration."""
    monkeypatch.chdir(tmp_path)
    recording = RecordingTransport()

    def build(**kwargs: Any) -> RecordingTransport:
        recording.arguments = kwargs
        return recording

    monkeypatch.setattr(cli, "UrllibTransport", build)
    return recording


def test_info_describes_a_file_share(transport: RecordingTransport) -> None:
    transport.queue([file_answer()])

    result = runner.invoke(app, ["info", FILE_URL])

    assert result.exit_code == 0
    assert result.stdout.strip() == (
        "Provider: Mega\nType: File\nName: ubuntu.iso\nSize: 5.8 GB\nAvailable: Yes"
    )


def test_info_downloads_nothing(transport: RecordingTransport) -> None:
    transport.queue([file_answer()])

    runner.invoke(app, ["info", FILE_URL])

    assert len(transport.calls) == 1
    assert transport.calls[0].command == {"a": "g", "p": "AaBbCcDd"}
    assert "g" not in transport.calls[0].command


def test_info_never_sends_the_key(transport: RecordingTransport) -> None:
    transport.queue([file_answer()])

    runner.invoke(app, ["info", FILE_URL])

    assert FILE_KEY not in transport.everything_sent()


def test_info_never_prints_the_key(transport: RecordingTransport) -> None:
    transport.queue([file_answer()])

    result = runner.invoke(app, ["info", FILE_URL])

    assert FILE_KEY not in result.output
    assert FILE_KEY[:12] not in result.output


def test_info_never_prints_the_key_as_json(transport: RecordingTransport) -> None:
    transport.queue([folder_answer()])

    result = runner.invoke(app, ["info", FOLDER_URL, "--json"])

    assert FOLDER_KEY not in result.output
    assert FOLDER_KEY[:12] not in result.output


def test_info_describes_a_folder_share(transport: RecordingTransport) -> None:
    transport.queue([folder_answer()])

    result = runner.invoke(app, ["info", FOLDER_URL])

    assert result.exit_code == 0
    assert "Type: Folder" in result.stdout
    assert "Name: Ubuntu Releases" in result.stdout
    assert "Files: 2" in result.stdout
    assert "Folders: 1" in result.stdout
    assert "archive/" in result.stdout
    assert "ubuntu.iso" in result.stdout
    assert "5.8 GB" in result.stdout


def test_info_reports_a_truncated_listing(transport: RecordingTransport) -> None:
    transport.queue([folder_answer()])

    result = runner.invoke(app, ["info", FOLDER_URL, "--max-entries", "1"])

    assert "more entries were not listed" in result.stdout


def test_info_describes_an_entry_inside_a_folder(transport: RecordingTransport) -> None:
    transport.queue([folder_answer()])

    result = runner.invoke(app, ["info", f"{FOLDER_URL}/file/{CHILD_FILE_HANDLE}"])

    assert result.exit_code == 0
    assert "Type: File" in result.stdout
    assert "Name: ubuntu.iso" in result.stdout


def test_info_reports_an_unreadable_name(transport: RecordingTransport) -> None:
    transport.queue([file_answer()])

    result = runner.invoke(app, ["info", file_url()])

    assert result.exit_code == 0
    assert "Name: unavailable (encrypted)" in result.stdout
    assert "Names stay encrypted" in result.stdout


def test_info_prints_json(transport: RecordingTransport) -> None:
    transport.queue([file_answer()])

    result = runner.invoke(app, ["info", FILE_URL, "--json"])

    document = json.loads(result.stdout)
    assert document["provider"] == "mega"
    assert document["name"] == "ubuntu.iso"
    assert document["size"] == 5_800_000_000
    assert document["available"] is True
    assert document["has_key"] is True
    assert document["url"] == "https://mega.nz/file/AaBbCcDd"


def test_json_of_a_folder_lists_its_entries(transport: RecordingTransport) -> None:
    transport.queue([folder_answer()])

    result = runner.invoke(app, ["info", FOLDER_URL, "--json"])

    document = json.loads(result.stdout)
    assert document["file_count"] == 2
    assert [entry["name"] for entry in document["entries"]] == [
        "archive",
        "checksums.txt",
        "ubuntu.iso",
    ]


def test_offline_mode_contacts_nobody(transport: RecordingTransport) -> None:
    result = runner.invoke(app, ["info", FILE_URL, "--offline"])

    assert result.exit_code == 0
    assert transport.calls == []
    assert "Provider: Mega" in result.stdout
    assert "Type: File" in result.stdout
    assert "Available: Unknown" in result.stdout


def test_offline_mode_recognises_a_folder(transport: RecordingTransport) -> None:
    result = runner.invoke(app, ["info", FOLDER_URL, "--offline"])

    assert result.exit_code == 0
    assert "Type: Folder" in result.stdout


def test_a_missing_resource_exits_with_its_own_code(transport: RecordingTransport) -> None:
    transport.queue([-9])

    result = runner.invoke(app, ["info", FILE_URL])

    assert result.exit_code == EXIT_UNAVAILABLE
    assert "Available: No (not found)" in result.stdout


def test_a_blocked_resource_is_reported(transport: RecordingTransport) -> None:
    transport.queue([-16])

    result = runner.invoke(app, ["info", FILE_URL])

    assert result.exit_code == EXIT_UNAVAILABLE
    assert "blocked by the provider" in result.stdout


def test_an_undetermined_answer_uses_a_separate_code(transport: RecordingTransport) -> None:
    transport.queue([-3], [-3], [-3])

    result = runner.invoke(app, ["info", FILE_URL])

    assert result.exit_code == EXIT_UNDETERMINED
    assert "rate limiting" in result.stdout


def test_a_provider_failure_is_reported_on_stderr(transport: RecordingTransport) -> None:
    transport.queue([{"nope": 1}])

    result = runner.invoke(app, ["info", FOLDER_URL])

    assert result.exit_code == EXIT_UNDETERMINED
    assert "Error:" in result.output


def test_an_ordinary_url_is_described_rather_than_rejected(
    transport: RecordingTransport, tmp_path: Path
) -> None:
    """It used to answer "no provider can describe this link" for most of the web.

    An inspection is one HEAD, so describing a plain file costs nothing and
    moves nothing -- which is why this command gets a file transport while
    still having no way at all to transfer.
    """
    site = Site()
    site.add("/report.pdf", body=b"%PDF-1.4" + b"z" * 900, content_type="application/pdf")
    (tmp_path / "maxicrawler.toml").write_text(
        "[maxicrawler]\nallow_private_networks = true\n", encoding="utf-8"
    )

    with serve(site) as base:
        result = runner.invoke(app, ["info", f"{base}/report.pdf"])

    assert "no provider can describe this link" not in result.output
    assert "report.pdf" in result.stdout


def test_an_installation_that_declines_arbitrary_files_says_so(
    transport: RecordingTransport, tmp_path: Path
) -> None:
    """`direct_downloads = false` reaches this command too.

    Having said it does not fetch arbitrary files, an installation should not
    find this one describing them either.
    """
    (tmp_path / "maxicrawler.toml").write_text(
        "[maxicrawler]\ndirect_downloads = false\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["info", "https://example.test/file/AaBbCcDd"])

    assert result.exit_code != 0


def test_a_url_that_is_not_absolute_is_rejected(transport: RecordingTransport) -> None:
    result = runner.invoke(app, ["info", "not-a-url"])

    assert result.exit_code != 0
    assert "absolute HTTP(S) URL" in result.output


def test_a_rejected_link_is_echoed_without_its_fragment(
    transport: RecordingTransport,
) -> None:
    result = runner.invoke(app, ["info", "https://example.test/x#SuperSecretKeyMaterial"])

    assert result.exit_code != 0
    assert "SuperSecretKeyMaterial" not in result.output


def test_info_uses_the_configured_network_settings(
    transport: RecordingTransport, tmp_path: Path
) -> None:
    config = tmp_path / "custom.toml"
    config.write_text(
        '[maxicrawler]\nuser_agent = "Scanner/9"\nnetwork_timeout = 2.5\n', encoding="utf-8"
    )
    transport.queue([file_answer()])

    result = runner.invoke(app, ["info", FILE_URL, "--config", str(config)])

    assert result.exit_code == 0
    assert transport.arguments == {"user_agent": "Scanner/9", "timeout": 2.5}


def test_info_honours_a_configured_entry_limit(
    transport: RecordingTransport, tmp_path: Path
) -> None:
    config = tmp_path / "custom.toml"
    config.write_text("[maxicrawler]\nmax_entries = 1\n", encoding="utf-8")
    transport.queue([folder_answer()])

    result = runner.invoke(app, ["info", FOLDER_URL, "--config", str(config)])

    assert "more entries were not listed" in result.stdout
