"""Turning a score page into the files worth keeping.

The transport is a stub, so every assertion here is about what the provider
makes of an answer rather than about how the answer arrived. That split is the
same one ``tests/test_download_manager.py`` draws and for the same reason: the
questions worth asking of this class are which renderings it picks, what it
calls them, and which failures it tells apart.

Whether a session reaches the wire and stops at a redirect is a property of the
transport, and lives in ``tests/test_transport_headers.py`` where a real socket
can answer it.
"""

from collections.abc import Generator

import pytest
from doubles import make_record
from musescore_fixtures import SCORE_ID, TITLE, challenge_page, login_page, page, state

from maxicrawler.domain import (
    ProviderCapability,
    ResourceInspection,
    ResourceKind,
    ResourceRef,
    UrlCategory,
    UrlClassification,
)
from maxicrawler.providers.errors import UnsupportedResourceError
from maxicrawler.providers.musescore import (
    ChallengeEncounteredError,
    MuseScoreProvider,
    ScorePageError,
    SessionExpiredError,
)
from maxicrawler.providers.transport import RemoteFile

SCORE_URL = "https://musescore.com/user/21965011/scores/4217351"
PDF = b"%PDF-1.4 a score\n"


class StubTransport:
    """A :class:`FileTransport` answering from a prepared map of URLs."""

    def __init__(self, bodies: dict[str, bytes], *, media_type: str = "text/html") -> None:
        self.bodies = bodies
        self.media_type = media_type
        self.asked: list[str] = []

    def head(self, url: str) -> RemoteFile:
        self.asked.append(url)
        return RemoteFile(url=url, status=200, size=len(self.bodies.get(url, b"")))

    def open(
        self, url: str, *, chunk_size: int = 65536
    ) -> tuple[RemoteFile, Generator[bytes, None, None]]:
        self.asked.append(url)
        body = self.bodies.get(url, b"")
        remote = RemoteFile(url=url, status=200, size=len(body), media_type=self.media_type)

        def chunks() -> Generator[bytes, None, None]:
            for start in range(0, len(body), chunk_size):
                yield body[start : start + chunk_size]

        return remote, chunks()


class Collecting:
    """A sink that keeps what it was given."""

    def __init__(self) -> None:
        self.content: object = None
        self.chunks: list[bytes] = []

    def begin(self, content: object) -> None:
        self.content = content

    def write(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    @property
    def payload(self) -> bytes:
        return b"".join(self.chunks)


def download_url(kind: str) -> str:
    """Return the address the fixture state names for *kind*."""
    return f"https://musescore.com/score/download/index?score_id={SCORE_ID}&type={kind}"


def serving(markup: str, **files: bytes) -> StubTransport:
    """Return a transport answering the score page with *markup*."""
    bodies = {SCORE_URL: markup.encode("utf-8")}
    bodies.update({download_url(kind): body for kind, body in files.items()})
    return StubTransport(bodies)


def classification(url: str = SCORE_URL) -> UrlClassification:
    """Return a classification naming *url* as a MuseScore container."""
    return UrlClassification(
        record=make_record(url), category=UrlCategory.CONTAINER, plugin_name="musescore"
    )


def inspected(provider: MuseScoreProvider) -> ResourceInspection:
    """Return the inspection of the standard fixture score."""
    return provider.inspect(provider.reference(classification()))


def test_a_score_is_inspected_into_the_wanted_renderings() -> None:
    provider = MuseScoreProvider(transport=serving(page(document=state())))

    inspection = inspected(provider)

    assert inspection.ref.kind is ResourceKind.FOLDER
    assert [entry.metadata.name for entry in inspection.entries] == [
        f"{TITLE} ({SCORE_ID}).pdf",
        f"{TITLE} ({SCORE_ID}).mscz",
    ]


def test_the_order_of_the_wanted_formats_decides_the_order_of_the_jobs() -> None:
    """PDF first by default, because it is the one worth having if the day runs out."""
    provider = MuseScoreProvider(transport=serving(page(document=state())), formats=("mscz", "pdf"))

    inspection = inspected(provider)

    assert [entry.metadata.name for entry in inspection.entries] == [
        f"{TITLE} ({SCORE_ID}).mscz",
        f"{TITLE} ({SCORE_ID}).pdf",
    ]


def test_every_entry_is_addressed_inside_the_score_it_came_from() -> None:
    """The score number is the container, which is what keeps two arrangements apart."""
    provider = MuseScoreProvider(transport=serving(page(document=state())))

    inspection = inspected(provider)

    assert all(entry.ref.parent_id == SCORE_ID for entry in inspection.entries)
    assert all(entry.ref.kind is ResourceKind.FILE for entry in inspection.entries)


def test_a_rendering_the_score_does_not_offer_is_simply_absent() -> None:
    """A PDF-only score is a score, not a failure."""
    provider = MuseScoreProvider(transport=serving(page(document=state(kinds=("pdf",)))))

    assert len(inspected(provider).entries) == 1


def test_a_score_offering_nothing_wanted_says_what_it_did_offer() -> None:
    provider = MuseScoreProvider(transport=serving(page(document=state(kinds=("mp3",)))))

    with pytest.raises(ScorePageError, match="mp3"):
        inspected(provider)


def test_the_allowance_the_page_stated_reaches_the_inspection() -> None:
    """Carried rather than acted on: a provider is the wrong place to plan a day."""
    provider = MuseScoreProvider(transport=serving(page(document=state())))

    inspection = inspected(provider)

    assert inspection.metadata is not None
    assert inspection.metadata.attribute("daily_limit") == "20"
    assert inspection.metadata.attribute("limit_reached") == "no"


def test_a_spent_allowance_is_carried_too() -> None:
    provider = MuseScoreProvider(transport=serving(page(document=state(limit_reached=True))))

    inspection = inspected(provider)

    assert inspection.metadata is not None
    assert inspection.metadata.attribute("limit_reached") == "yes"


def test_a_rendering_is_transferred_into_the_sink() -> None:
    provider = MuseScoreProvider(transport=serving(page(document=state()), pdf=PDF))
    inspection = inspected(provider)
    sink = Collecting()

    descriptor = provider.download(inspection.entries[0].ref, sink)

    assert sink.payload == PDF
    assert descriptor.name == f"{TITLE} ({SCORE_ID}).pdf"


def test_the_stored_name_comes_from_the_page_rather_than_the_response() -> None:
    """MuseScore answers a download URL with a generic filename.

    The title was read off the page during inspection, and it is the only place
    a readable name exists.
    """
    provider = MuseScoreProvider(transport=serving(page(document=state()), pdf=PDF))

    descriptor = provider.download(inspected(provider).entries[0].ref, Collecting())

    assert TITLE in (descriptor.name or "")


def test_a_score_without_a_title_falls_back_to_its_number() -> None:
    provider = MuseScoreProvider(transport=serving(page(document=state(title=None))))

    assert inspected(provider).entries[0].metadata.name == f"{SCORE_ID}.pdf"


def test_a_challenge_is_carried_up_rather_than_answered() -> None:
    provider = MuseScoreProvider(transport=serving(challenge_page()))

    with pytest.raises(ChallengeEncounteredError):
        inspected(provider)


def test_a_stale_session_is_told_apart_from_a_challenge() -> None:
    provider = MuseScoreProvider(transport=serving(login_page()))

    with pytest.raises(SessionExpiredError):
        inspected(provider)


def test_a_provider_without_a_transport_advertises_no_download() -> None:
    """The ordinary state of an installation nobody configured a session for."""
    provider = MuseScoreProvider()

    assert provider.metadata.supports(ProviderCapability.DOWNLOAD) is False
    assert provider.metadata.supports(ProviderCapability.INSPECT) is True


def test_the_page_itself_is_refused_as_a_transfer() -> None:
    """A score is two files; picking one silently would pick wrong half the time."""
    provider = MuseScoreProvider(transport=serving(page(document=state())))
    reference = provider.reference(classification())

    with pytest.raises(UnsupportedResourceError, match="no rendering"):
        provider.download(reference, Collecting())


def test_another_provider_s_reference_is_refused() -> None:
    provider = MuseScoreProvider(transport=serving(page(document=state())))
    foreign = ResourceRef(
        provider="direct", resource_id=SCORE_ID, kind=ResourceKind.FOLDER, url=SCORE_URL
    )

    with pytest.raises(UnsupportedResourceError, match="another provider"):
        provider.inspect(foreign)


def test_a_url_that_is_not_a_score_is_refused_a_reference() -> None:
    provider = MuseScoreProvider()

    with pytest.raises(UnsupportedResourceError, match="not a MuseScore"):
        provider.reference(classification("https://example.org/"))


def test_a_page_far_too_large_to_be_a_score_page_is_refused() -> None:
    """A redirect into something streamed must not be buffered for ever."""
    provider = MuseScoreProvider(transport=StubTransport({SCORE_URL: b"x" * (5 * 1024 * 1024)}))

    with pytest.raises(ScorePageError, match="too large"):
        inspected(provider)


def test_this_provider_claims_score_pages_and_nothing_else() -> None:
    provider = MuseScoreProvider()

    assert provider.supports(classification()) is True
    assert provider.supports(classification("https://musescore.com/sheetmusic/piano")) is False
    assert provider.supports(classification("https://example.org/scores/1")) is False


def test_asking_for_no_formats_at_all_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="at least one format"):
        MuseScoreProvider(formats=())
