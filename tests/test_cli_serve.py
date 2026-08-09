"""Tests for running the web interface from the command line.

Nothing here binds a socket. ``uvicorn.run`` is the last line of the command
and the only part that would, so it is replaced and its arguments inspected —
which is also the only thing worth asserting about it.
"""

import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from maxicrawler.cli import app
from maxicrawler.cli.serving import (
    EXIT_WEB_UNAVAILABLE,
    banner,
    exposure_notice,
    is_loopback,
    refusal,
    url_for,
)

runner = CliRunner()


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace ``uvicorn.run`` and record what it was asked to do."""
    import uvicorn

    calls: list[dict[str, Any]] = []

    def record(application: object, **kwargs: Any) -> None:
        calls.append({"application": application, **kwargs})

    monkeypatch.setattr(uvicorn, "run", record)
    return calls


# --- which addresses are this machine ----------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.53", "::1", "localhost", "0:0:0:0:0:0:0:1"])
def test_a_loopback_address_needs_no_permission(host: str) -> None:
    assert is_loopback(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "", "192.168.1.10", "10.0.0.4", "2001:db8::1"])
def test_anything_reachable_from_elsewhere_is_not_loopback(host: str) -> None:
    assert is_loopback(host) is False


def test_a_hostname_is_treated_as_remote_without_asking_a_resolver() -> None:
    """A name can resolve anywhere, and can start doing so tomorrow."""
    assert is_loopback("crawler.internal") is False
    assert is_loopback("example.test") is False


# --- what the operator is told -----------------------------------------------


def test_an_address_becomes_something_you_can_click() -> None:
    assert url_for("127.0.0.1", 8000) == "http://127.0.0.1:8000/"


def test_an_ipv6_address_is_bracketed() -> None:
    """Without the brackets the port reads as part of the address."""
    assert url_for("::1", 8000) == "http://[::1]:8000/"


@pytest.mark.parametrize("host", ["0.0.0.0", "::", ""])
def test_binding_everything_is_reported_as_reachable_from_here(host: str) -> None:
    """ "http://0.0.0.0:8000" is not an address anybody can visit."""
    assert url_for(host, 8000) == "http://localhost:8000/"
    assert "every interface" in banner(host, 8000)


def test_the_banner_says_where_to_look() -> None:
    assert banner("127.0.0.1", 9000) == "MaxiCrawler is listening on http://127.0.0.1:9000/"


def test_the_refusal_says_what_to_do_about_it() -> None:
    message = refusal("192.168.1.10")

    assert "192.168.1.10" in message
    assert "--allow-remote" in message
    assert "no authentication" in message


def test_the_notice_says_what_was_allowed() -> None:
    notice = exposure_notice("192.168.1.10", 8000)

    assert "192.168.1.10" in notice
    assert "8000" in notice
    assert "no authentication" in notice.replace("\n", " ")


def test_the_notice_for_every_interface_does_not_name_a_meaningless_address() -> None:
    assert "0.0.0.0" not in exposure_notice("0.0.0.0", 8000)


# --- the command --------------------------------------------------------------


def test_serving_locally_needs_no_permission(served: list[dict[str, Any]]) -> None:
    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0
    assert served[0]["host"] == "127.0.0.1"
    assert served[0]["port"] == 8000
    assert "http://127.0.0.1:8000/" in result.stdout


def test_the_host_and_port_are_passed_through(served: list[dict[str, Any]]) -> None:
    result = runner.invoke(app, ["serve", "--host", "::1", "--port", "9123"])

    assert result.exit_code == 0
    assert served[0]["host"] == "::1"
    assert served[0]["port"] == 9123


def test_a_remote_address_is_refused(served: list[dict[str, Any]]) -> None:
    result = runner.invoke(app, ["serve", "--host", "0.0.0.0"])

    assert result.exit_code != 0
    assert "--allow-remote" in result.output
    assert served == []


def test_a_remote_address_can_be_asked_for(served: list[dict[str, Any]]) -> None:
    result = runner.invoke(app, ["serve", "--host", "0.0.0.0", "--allow-remote"])

    assert result.exit_code == 0
    assert served[0]["host"] == "0.0.0.0"  # noqa: S104 - the point of the test
    assert "no authentication" in result.output.replace("\n", " ")


def test_a_private_address_is_refused_too(served: list[dict[str, Any]]) -> None:
    """A LAN is not this machine, however trusted it feels."""
    result = runner.invoke(app, ["serve", "--host", "192.168.1.10"])

    assert result.exit_code != 0
    assert served == []


# --- the configuration it serves under ---------------------------------------


def test_the_server_reads_the_configuration_it_was_given(
    served: list[dict[str, Any]], tmp_path: Path
) -> None:
    config = tmp_path / "maxicrawler.toml"
    config.write_text(
        f'[maxicrawler]\ncrawl_max_pages = 7\ndatabase_path = "{(tmp_path / "u.db").as_posix()}"\n',
        encoding="utf-8",
    )

    result = runner.invoke(app, ["serve", "--config", str(config)])

    assert result.exit_code == 0
    application = served[0]["application"]
    assert application.state.crawl_service.settings.crawl_max_pages == 7


def test_the_settings_page_can_name_the_file_it_was_given(
    served: list[dict[str, Any]], tmp_path: Path
) -> None:
    """The CLI read the file, so the page must point at that one, not the default."""
    config = tmp_path / "elsewhere.toml"
    config.write_text("[maxicrawler]\n", encoding="utf-8")

    runner.invoke(app, ["serve", "--config", str(config)])

    assert served[0]["application"].state.config_path == config


# --- when the interface is not installed --------------------------------------


def test_a_missing_extra_is_a_sentence_rather_than_an_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ModuleNotFoundError: uvicorn` is accurate and useless."""
    monkeypatch.setitem(sys.modules, "uvicorn", None)

    result = runner.invoke(app, ["serve"])

    assert result.exit_code == EXIT_WEB_UNAVAILABLE
    assert "extra" in result.output
    assert "pip install" in result.output


def test_a_missing_extra_is_refused_before_anything_is_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "uvicorn", None)

    assert runner.invoke(app, ["serve"]).exit_code == EXIT_WEB_UNAVAILABLE


def test_the_exit_code_for_a_missing_interface_is_its_own() -> None:
    """Distinct from every other command's, so a script can tell them apart."""
    from maxicrawler.cli import crawling, downloads, inspection

    others = {
        crawling.EXIT_FETCH_FAILED,
        crawling.EXIT_NOT_A_PAGE,
        crawling.EXIT_INTERRUPTED,
        downloads.EXIT_DOWNLOADS_INCOMPLETE,
        inspection.EXIT_UNAVAILABLE,
        inspection.EXIT_UNDETERMINED,
    }

    assert EXIT_WEB_UNAVAILABLE not in others


# --- the command exists and describes itself ---------------------------------


def test_serve_is_listed_among_the_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert "serve" in result.output


def test_the_help_says_where_it_listens_and_what_that_means() -> None:
    result = runner.invoke(app, ["serve", "--help"])

    assert "127.0.0.1" in result.output
    assert "--allow-remote" in result.output
