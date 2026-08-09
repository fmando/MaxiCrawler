"""Tests for the HTTP application and its optional dependencies."""

import ast
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from maxicrawler.api import WebDependencyError, create_app
from maxicrawler.api.application import MISSING_EXTRA, health
from maxicrawler.app import CrawlService
from maxicrawler.config import Settings


def make_client() -> TestClient:
    """Return a client over an application with an injected service."""
    service = CrawlService(Settings(user_agent="MaxiCrawler/test"))
    return TestClient(create_app(service=service))


def test_the_application_answers_that_it_is_alive() -> None:
    with make_client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_an_unknown_path_is_not_found() -> None:
    with make_client() as client:
        assert client.get("/nothing-here").status_code == 404


def test_the_application_holds_the_service_it_was_given() -> None:
    service = CrawlService(Settings(user_agent="MaxiCrawler/test"))

    application = create_app(service=service)

    assert application.state.crawl_service is service


def test_the_application_builds_its_own_service_from_settings() -> None:
    settings = Settings(user_agent="MaxiCrawler/configured")

    application = create_app(settings=settings)

    assert application.state.crawl_service.settings.user_agent == "MaxiCrawler/configured"


def test_the_application_falls_back_to_the_default_configuration() -> None:
    application = create_app()

    assert isinstance(application.state.crawl_service, CrawlService)


# --- the optional extra ------------------------------------------------------


def test_importing_the_package_never_needs_the_extra() -> None:
    """A boundary test or a core package must be able to look without installing."""
    import maxicrawler.api

    assert maxicrawler.api.WebDependencyError is WebDependencyError


def test_asking_for_something_this_package_does_not_have_says_so() -> None:
    import maxicrawler.api

    with pytest.raises(AttributeError, match="has no attribute 'nonsense'"):
        maxicrawler.api.nonsense  # noqa: B018


def test_the_missing_extra_message_names_the_command() -> None:
    """A ModuleNotFoundError on an import line would be accurate and useless."""
    assert "web" in MISSING_EXTRA
    assert "uv sync --extra web" in MISSING_EXTRA
    assert "pip install" in MISSING_EXTRA


def test_the_dependency_error_is_a_web_interface_error() -> None:
    from maxicrawler.api.errors import WebInterfaceError

    assert issubclass(WebDependencyError, WebInterfaceError)


def test_starlette_is_imported_behind_the_guard() -> None:
    """Read from the syntax tree, so the guard cannot be quietly removed.

    Every Starlette import in the application module has to sit inside the
    try/except that turns a missing extra into a sentence.
    """
    tree = ast.parse(Path("src/maxicrawler/api/application.py").read_text(encoding="utf-8"))
    guarded = {
        node
        for handler in ast.walk(tree)
        if isinstance(handler, ast.Try)
        for node in ast.walk(handler)
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("starlette"):
            assert node in guarded, f"unguarded starlette import on line {node.lineno}"


def test_the_health_route_is_a_coroutine() -> None:
    import inspect

    assert inspect.iscoroutinefunction(health)
