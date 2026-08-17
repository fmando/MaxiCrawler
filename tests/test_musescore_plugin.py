"""Recognising a score address, and declining everything else on the domain.

A plugin that claimed the whole host would put the pricing page and every user
profile in front of a provider that can only read score pages. The declining is
therefore as much the subject here as the recognising.
"""

from doubles import make_record

from maxicrawler.domain import UrlCategory
from maxicrawler.plugins.musescore import MuseScorePlugin, parse_score_url

SCORE = "https://musescore.com/user/21965011/scores/4217351"


def classify(url: str) -> UrlCategory:
    """Return what the plugin makes of *url*."""
    return MuseScorePlugin().classify(make_record(url)).category


def test_a_score_page_is_a_container() -> None:
    """One page is one piece of music in several renderings, not one file.

    Saying container is what lets the planner turn one address into the several
    jobs it really is, without the download chain learning this host exists.
    """
    assert classify(SCORE) is UrlCategory.CONTAINER


def test_the_score_number_is_carried_into_the_classification() -> None:
    classification = MuseScorePlugin().classify(make_record(SCORE))

    attributes = {attribute.name: attribute.value for attribute in classification.attributes}
    assert attributes["score_id"] == "4217351"
    assert attributes["score_url"] == SCORE


def test_a_language_subdomain_is_the_same_score() -> None:
    link = parse_score_url("https://ja.musescore.com/user/21965011/scores/4217351")

    assert link is not None
    assert link.score_id == "4217351"


def test_a_vanity_profile_addresses_the_same_score() -> None:
    """``/michael_sisley/scores/4217351`` is the canonical score under another name."""
    link = parse_score_url("https://musescore.com/michael_sisley/scores/4217351")

    assert link is not None
    assert link.score_id == "4217351"


def test_a_view_of_a_score_reduces_to_the_score() -> None:
    """An embed is the same music with a different player around it.

    Left alone, the same piece would be downloaded twice under two names.
    """
    link = parse_score_url(f"{SCORE}/embed")

    assert link is not None
    assert link.url == SCORE


def test_everything_else_on_the_domain_is_declined() -> None:
    plugin = MuseScorePlugin()
    for url in (
        "https://musescore.com/",
        "https://musescore.com/user/21965011",
        "https://musescore.com/sheetmusic/piano",
        "https://musescore.com/scores/browse",
        "https://musescore.com/artist/leonard_cohen-135209",
    ):
        assert plugin.can_handle(make_record(url)) is False, url


def test_another_host_is_declined_however_its_path_reads() -> None:
    assert parse_score_url("https://example.org/user/1/scores/4217351") is None
    assert parse_score_url("https://musescore.com.evil.example/user/1/scores/1") is None


def test_a_declined_url_classifies_as_unsupported_rather_than_raising() -> None:
    """The protocol says callers may ask about records the plugin does not handle."""
    assert classify("https://example.org/") is UrlCategory.UNSUPPORTED
