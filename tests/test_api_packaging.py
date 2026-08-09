"""Tests that the interface still works once it is installed rather than run.

A web interface is the first part of this project that is not only Python. The
pages and the stylesheet are data files, and data files are what a wheel quietly
leaves out — so each way that can happen is checked here:

* the pages are found relative to the code, not to whatever directory the
  server was started from;
* nothing under the package is invisible to the build;
* nothing on a page is fetched from another host, which is what would turn a
  working installation into one that needs the internet to draw itself.
"""

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from maxicrawler.api import create_app
from maxicrawler.api import routes as api_routes
from maxicrawler.app import CrawlService
from maxicrawler.config import Settings

PACKAGE = Path("src/maxicrawler")
API = PACKAGE / "api"
TEMPLATES = API / "templates"
STATIC = API / "static"


@pytest.fixture
def test_client(tmp_path: Path) -> TestClient:
    """Return a client over an application that stores nothing anywhere else."""
    settings = Settings(user_agent="MaxiCrawler/test", database_path=tmp_path / "maxicrawler.db")
    return TestClient(create_app(service=CrawlService(settings)))


# --- found beside the code ----------------------------------------------------


def test_the_pages_live_inside_the_package() -> None:
    """Anything outside it is not in the wheel at all."""
    assert TEMPLATES.is_dir()
    assert STATIC.is_dir()
    assert api_routes.STATIC_DIRECTORY.resolve().is_relative_to(
        Path(api_routes.__file__).parent.resolve()
    )


def test_the_pages_are_found_from_any_working_directory(
    test_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server is started from wherever the operator happens to be standing.

    A template directory resolved against the current directory works in the
    repository and nowhere else, which is a failure that never shows up until
    somebody installs it.
    """
    monkeypatch.chdir(tmp_path)

    with test_client as client:
        page = client.get("/")
        stylesheet = client.get("/static/maxicrawler.css")

    assert page.status_code == 200
    assert stylesheet.status_code == 200


def test_every_page_the_routes_name_exists() -> None:
    """A typo in a template name is a 500 on one page and nothing anywhere else."""
    named = set(re.findall(r'"([\w.]+\.html)"', (API / "routes.py").read_text(encoding="utf-8")))

    assert named
    assert {name for name in named if not (TEMPLATES / name).is_file()} == set()


def test_every_page_a_page_pulls_in_exists() -> None:
    """`{% extends %}` and `{% include %}` are the other half of the same typo."""
    pulled_in = {
        name
        for template in TEMPLATES.glob("*.html")
        for name in re.findall(
            r'{%-?\s*(?:extends|include)\s+"([^"]+)"', template.read_text(encoding="utf-8")
        )
    }

    assert "base.html" in pulled_in, "read no reference at all, so this proves nothing"
    assert {name for name in pulled_in if not (TEMPLATES / name).is_file()} == set()


# --- carried into the wheel ---------------------------------------------------


def tracked_files(directory: Path) -> set[str]:
    """Return what version control knows about under *directory*."""
    listed = subprocess.run(  # noqa: S603 - a literal command, no shell
        ["git", "ls-files", "--", directory.as_posix()],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return {line for line in listed.stdout.splitlines() if line}


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git to read the file list")
def test_every_file_of_the_interface_is_visible_to_the_build() -> None:
    """Hatchling excludes what version control ignores.

    So a stylesheet caught by a broad ``.gitignore`` rule would keep working in
    the repository and be missing from every installation — the one packaging
    failure that a test run cannot otherwise see.
    """
    on_disk = {
        path.as_posix()
        for path in API.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }

    assert on_disk - tracked_files(API) == set()


def test_the_wheel_carries_the_whole_package() -> None:
    """No include list, because a list is what a new data file falls off."""
    configuration = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    wheel = configuration["tool"]["hatch"]["build"]["targets"]["wheel"]

    assert wheel["packages"] == ["src/maxicrawler"]
    assert "include" not in wheel
    assert "exclude" not in wheel


def test_the_interface_is_an_extra_rather_than_a_dependency() -> None:
    """Installing MaxiCrawler must not install a web server nobody asked for."""
    configuration = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    project = configuration["project"]

    assert not [name for name in project["dependencies"] if "starlette" in name]
    assert {name.partition(">")[0] for name in project["optional-dependencies"]["web"]} == {
        "jinja2",
        "starlette",
        "uvicorn",
    }


# --- drawn without the internet -----------------------------------------------


def test_nothing_on_a_page_is_loaded_from_another_host() -> None:
    """The stylesheet and the script are served from this process or not at all.

    A CDN would make the interface depend on somebody else's uptime to render,
    and would tell that somebody every time a page is opened.
    """
    loaded = [
        (template.name, attribute, value)
        for template in TEMPLATES.glob("*.html")
        # A value holding `{{ ... }}` is a URL somebody crawled, not one this
        # page loads, so the literal ones are what there is to check.
        for attribute, value in re.findall(
            r'\b(src|href)="([^"{}]*)"', template.read_text(encoding="utf-8")
        )
    ]

    assert loaded, "found no literal link at all, so this proves nothing"
    assert [entry for entry in loaded if re.match(r"(https?:)?//", entry[2])] == []


def test_the_stylesheet_pulls_in_nothing_either() -> None:
    """A web font is a request to another host wearing a different hat."""
    css = (STATIC / "maxicrawler.css").read_text(encoding="utf-8")

    assert "@import" not in css
    assert not re.search(r"url\(\s*['\"]?(https?:)?//", css)
