"""Tests for refusing a state-changing request that came from somewhere else.

The rule is checked twice over: once as a function, because it is a rule and
reads as one, and once through a real POST route, because a rule nobody wired
in protects nothing.
"""

import pytest
from starlette.datastructures import Headers
from starlette.testclient import TestClient

from maxicrawler.api import create_app
from maxicrawler.api.origin import ACCEPTED_SITES, SAFE_METHODS, is_ours
from maxicrawler.app import CrawlService
from maxicrawler.config import Settings

ELSEWHERE = "https://attacker.test"


def make_client() -> TestClient:
    """Return a client over an application with an injected service."""
    service = CrawlService(Settings(user_agent="MaxiCrawler/test"))
    return TestClient(create_app(service=service))


def headers(**values: str) -> Headers:
    """Return request headers spelled the way a browser sends them."""
    return Headers({name.replace("_", "-"): value for name, value in values.items()})


# --- the rule ----------------------------------------------------------------


@pytest.mark.parametrize("site", sorted(ACCEPTED_SITES))
def test_a_request_the_browser_calls_ours_is_allowed(site: str) -> None:
    assert is_ours(headers(sec_fetch_site=site)) is True


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_a_request_the_browser_calls_foreign_is_refused(site: str) -> None:
    assert is_ours(headers(sec_fetch_site=site)) is False


def test_the_fetch_header_is_believed_over_the_origin() -> None:
    """A browser that sends both has already answered the question.

    The header the browser sets and script cannot reach is the better of the
    two, so a matching `Origin` does not rescue a `cross-site` fetch.
    """
    assert is_ours(headers(sec_fetch_site="cross-site", origin="http://here", host="here")) is False


def test_an_origin_of_ours_is_allowed_without_the_fetch_header() -> None:
    assert is_ours(headers(origin="http://127.0.0.1:8000", host="127.0.0.1:8000")) is True


def test_an_origin_that_is_not_ours_is_refused() -> None:
    assert is_ours(headers(origin=ELSEWHERE, host="127.0.0.1:8000")) is False


def test_an_opaque_origin_is_refused() -> None:
    """What a sandboxed frame sends. It is not our host, so it is not ours."""
    assert is_ours(headers(origin="null", host="127.0.0.1:8000")) is False


def test_a_host_is_compared_without_regard_to_case() -> None:
    assert is_ours(headers(origin="http://Wiki.Local:8000", host="wiki.local:8000")) is True


def test_a_client_that_sends_neither_header_is_allowed() -> None:
    """A script or a terminal, not a browser being pointed at us.

    Deliberate: a browser attaches one of the two to every cross-origin POST,
    so refusing this would break every non-browser client to guard against an
    attacker who could as easily send nothing at all.
    """
    assert is_ours(headers(host="127.0.0.1:8000")) is True


# --- the rule, wired in ------------------------------------------------------


def test_a_form_from_another_site_changes_nothing() -> None:
    with make_client() as client:
        response = client.post(
            "/crawls",
            data={"url": "https://example.test/"},
            headers={"Sec-Fetch-Site": "cross-site", "Origin": ELSEWHERE},
        )

    assert response.status_code == 403
    assert "did not come from a MaxiCrawler page" in response.text


def test_a_form_from_one_of_our_pages_is_carried_out() -> None:
    with make_client() as client:
        response = client.post(
            "/crawls",
            data={"url": "https://example.test/"},
            headers={"Sec-Fetch-Site": "same-origin"},
            follow_redirects=False,
        )

    assert response.status_code == 303


@pytest.mark.parametrize("path", ["/", "/library", "/downloads", "/health"])
def test_reading_a_page_is_never_refused(path: str) -> None:
    """Every safe method is let through whatever it says about itself."""
    with make_client() as client:
        response = client.get(path, headers={"Sec-Fetch-Site": "cross-site"})

    assert response.status_code == 200


def test_a_refused_request_never_reaches_the_application() -> None:
    """The queue is untouched, not merely the answer changed."""
    with make_client() as client:
        client.post(
            "/downloads",
            data={"url": "https://mega.nz/file/AaBbCcDd"},
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        queue = client.app.state.downloads  # type: ignore[attr-defined]

    assert queue.snapshot().waiting == ()


def test_the_safe_methods_are_the_ones_that_change_nothing() -> None:
    assert sorted(SAFE_METHODS) == ["GET", "HEAD", "OPTIONS"]
