"""Reading the state a score page carries, and knowing when it is not one.

Three answers come back from that host and only one of them is a score page.
Telling them apart is the whole value here: a challenge and a stale session
look similar from a distance and have completely different remedies, and a
queue that confused them would spend a day's allowance learning nothing.
"""

import pytest
from musescore_fixtures import SCORE_ID, TITLE, challenge_page, login_page, page, state

from maxicrawler.providers.musescore import (
    ChallengeEncounteredError,
    ScorePageError,
    SessionExpiredError,
    parse_score_page,
)

URL = "https://musescore.com/user/21965011/scores/4217351"


def read(markup: str) -> object:
    """Return the state parsed out of *markup*."""
    return parse_score_page(markup, url=URL)


def test_a_score_page_yields_its_downloads() -> None:
    parsed = parse_score_page(page(), url=URL)

    assert parsed.score_id == SCORE_ID
    assert [download.kind for download in parsed.downloads] == ["mscz", "pdf", "mid"]
    assert parsed.download_for("pdf") is not None


def test_the_allowance_is_read_rather_than_assumed() -> None:
    """The page states the limit, which is what lets a queue keep it.

    A configured number would be a guess about somebody else's rule; this is
    the rule, and it arrives with every page.
    """
    parsed = parse_score_page(page(), url=URL)

    assert parsed.daily_limit == 20
    assert parsed.limit_reached is False


def test_a_spent_allowance_is_visible_before_anything_is_attempted() -> None:
    parsed = parse_score_page(page(document=state(limit_reached=True)), url=URL)

    assert parsed.limit_reached is True


def test_the_descriptive_fields_are_carried_across() -> None:
    parsed = parse_score_page(page(), url=URL)

    assert parsed.title == TITLE
    assert parsed.pages == 2
    assert parsed.author is not None


def test_the_attribute_is_found_by_shape_rather_than_by_name() -> None:
    """The real attribute's name is a digest that changes between deployments.

    A parser matching on the name would break on a Tuesday, so this pins the
    behaviour that keeps it working: any ``data-`` attribute holding a state
    document will do.
    """
    markup = page().replace("data-e22a3776", "data-ffffffff")

    assert parse_score_page(markup, url=URL).score_id == SCORE_ID


def test_other_data_attributes_do_not_confuse_the_search() -> None:
    """Several attributes match the shape; the one with a store is the state."""
    decoy = '<div data-aaaaaaaabbbbbbbbccccccccdddddddd="{&quot;other&quot;: 1}"></div>'

    assert parse_score_page(page(extra=decoy), url=URL).score_id == SCORE_ID


def test_a_challenge_is_recognised_and_not_answered() -> None:
    """Meeting a bot check is where this program stops.

    Solving one is a non-goal, so the only correct behaviour is to say what
    happened in a way the caller can act on.
    """
    with pytest.raises(ChallengeEncounteredError, match="bot check"):
        read(challenge_page())


def test_a_logged_out_page_is_told_apart_from_a_challenge() -> None:
    """Different remedies: a new session fixes one and nothing fixes the other."""
    with pytest.raises(SessionExpiredError, match="session"):
        read(login_page())


def test_a_page_rendered_for_nobody_is_a_stale_session() -> None:
    """The state is present and says the reader is not signed in."""
    with pytest.raises(SessionExpiredError):
        read(page(document=state(authenticated=False)))


def test_a_page_without_state_is_a_shape_change_rather_than_a_session_problem() -> None:
    """Blaming the session for a redesign would send somebody to the wrong fix."""
    with pytest.raises(ScorePageError, match="no score state"):
        read("<html><body>something else entirely</body></html>")


def test_a_state_offering_no_downloads_is_refused() -> None:
    with pytest.raises(ScorePageError, match="no downloads"):
        read(page(document=state(kinds=())))


def test_a_malformed_download_row_does_not_condemn_the_page() -> None:
    """The list has carried placeholder shapes before; one odd row is not a failure."""
    document = state()
    rows = document["store"]["page"]["data"]["type_download_list"]
    rows.append({"type": "", "url": ""})
    rows.append("not a row")

    parsed = parse_score_page(page(document=document), url=URL)

    assert [download.kind for download in parsed.downloads] == ["mscz", "pdf", "mid"]


def test_an_absent_allowance_is_unknown_rather_than_zero() -> None:
    """A missing field means the page did not say, which is not the same as none."""
    parsed = parse_score_page(page(document=state(daily_limit=None)), url=URL)

    assert parsed.daily_limit is None


def test_a_state_that_is_not_valid_json_is_stepped_over() -> None:
    """One broken attribute must not hide a good one further down the page."""
    broken = '<div data-11111111222222223333333344444444="{not json"></div>'

    assert parse_score_page(broken + page(), url=URL).score_id == SCORE_ID


def test_the_score_number_is_required() -> None:
    document = state()
    del document["store"]["page"]["data"]["score"]["id"]

    with pytest.raises(ScorePageError, match="names no score"):
        read(page(document=document))


def test_the_fixture_really_hides_its_state_in_an_attribute() -> None:
    """Guards the fixture itself.

    If ``page()`` ever emitted plain JSON rather than an HTML-escaped
    attribute, every test above would still pass while proving nothing about
    the markup a browser is actually served.
    """
    markup = page()

    assert "&quot;store&quot;" in markup
    assert '"store"' not in markup
