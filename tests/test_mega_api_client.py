"""Tests for the Mega client/server API client."""

import pytest
from mega_fixtures import (
    FILE_HANDLE,
    SHARE_HANDLE,
    FailingTransport,
    RecordingTransport,
    file_answer,
    folder_answer,
)

from maxicrawler.providers import (
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderTransportError,
    Retrier,
    RetryPolicy,
)
from maxicrawler.providers.mega import MEGA_API_URL, MegaApiClient, MegaApiError


def client(transport: object, **kwargs: object) -> MegaApiClient:
    """Return a client whose retries never actually wait."""
    retrier = Retrier(RetryPolicy(max_attempts=3, initial_delay=0.0), sleep=lambda _: None)
    return MegaApiClient(transport, retrier=retrier, **kwargs)  # type: ignore[arg-type]


def test_file_metadata_sends_the_get_command() -> None:
    transport = RecordingTransport([[file_answer()]])

    client(transport).file_metadata(FILE_HANDLE)

    assert transport.calls[0].command == {"a": "g", "p": FILE_HANDLE}


def test_file_metadata_does_not_request_a_download_url() -> None:
    transport = RecordingTransport([[file_answer()]])

    client(transport).file_metadata(FILE_HANDLE)

    assert "g" not in transport.calls[0].command


def test_file_metadata_returns_size_and_attributes() -> None:
    transport = RecordingTransport([[file_answer(size=1234)]])

    answer = client(transport).file_metadata(FILE_HANDLE)

    assert answer["s"] == 1234
    assert isinstance(answer["at"], str)


def test_requests_go_to_the_public_endpoint() -> None:
    transport = RecordingTransport([[file_answer()]])

    client(transport).file_metadata(FILE_HANDLE)

    assert transport.calls[0].url == MEGA_API_URL


def test_a_custom_endpoint_is_honoured() -> None:
    transport = RecordingTransport([[file_answer()]])

    client(transport, base_url="https://eu.api.mega.co.nz/cs").file_metadata(FILE_HANDLE)

    assert transport.calls[0].url == "https://eu.api.mega.co.nz/cs"


def test_requests_carry_an_increasing_sequence_number() -> None:
    transport = RecordingTransport([[file_answer()], [file_answer()]])
    api = client(transport)

    api.file_metadata(FILE_HANDLE)
    api.file_metadata(FILE_HANDLE)

    assert [call.params["id"] for call in transport.calls] == ["1", "2"]


def test_requests_never_carry_a_session_identifier() -> None:
    transport = RecordingTransport([[file_answer()]])

    client(transport).file_metadata(FILE_HANDLE)

    assert "sid" not in transport.calls[0].params


def test_folder_nodes_sends_the_recursive_listing_command() -> None:
    transport = RecordingTransport([[folder_answer()]])

    client(transport).folder_nodes(SHARE_HANDLE)

    assert transport.calls[0].command == {"a": "f", "c": 1, "r": 1}


def test_folder_nodes_puts_the_share_into_the_query_string() -> None:
    transport = RecordingTransport([[folder_answer()]])

    client(transport).folder_nodes(SHARE_HANDLE)

    assert transport.calls[0].params["n"] == SHARE_HANDLE


def test_folder_nodes_returns_every_node() -> None:
    transport = RecordingTransport([[folder_answer()]])

    nodes = client(transport).folder_nodes(SHARE_HANDLE)

    assert len(nodes) == 4
    assert nodes[0]["h"] == SHARE_HANDLE


def test_folder_nodes_skips_entries_that_are_not_objects() -> None:
    transport = RecordingTransport([[{"f": [{"h": "AaBbCcDd", "t": 0}, "junk", 7]}]])

    nodes = client(transport).folder_nodes(SHARE_HANDLE)

    assert len(nodes) == 1


def test_folder_nodes_rejects_a_listing_without_a_node_array() -> None:
    transport = RecordingTransport([[{"nope": 1}]])

    with pytest.raises(ProviderProtocolError, match="does not contain a node array"):
        client(transport).folder_nodes(SHARE_HANDLE)


@pytest.mark.parametrize("answer", [-9, [-9]])
def test_a_negative_code_becomes_an_api_error(answer: object) -> None:
    transport = RecordingTransport([answer])

    with pytest.raises(MegaApiError, match=r"error -9 \(ENOENT\)") as failure:
        client(transport).file_metadata(FILE_HANDLE)

    assert failure.value.code == -9


def test_an_unknown_negative_code_is_still_reported() -> None:
    transport = RecordingTransport([[-99]])

    with pytest.raises(MegaApiError, match=r"error -99 \(EUNKNOWN\)"):
        client(transport).file_metadata(FILE_HANDLE)


@pytest.mark.parametrize("code", [-3, -4, -6])
def test_a_deferral_is_retried_and_then_reported(code: int) -> None:
    transport = RecordingTransport([[code], [code], [code]])

    with pytest.raises(ProviderRateLimitError, match="Mega deferred the request"):
        client(transport).file_metadata(FILE_HANDLE)

    assert len(transport.calls) == 3


def test_a_deferral_that_clears_is_not_reported() -> None:
    transport = RecordingTransport([[-3], [file_answer()]])

    answer = client(transport).file_metadata(FILE_HANDLE)

    assert answer["s"] == 5_800_000_000
    assert len(transport.calls) == 2


def test_a_transport_failure_is_retried() -> None:
    transport = FailingTransport(
        [ProviderTransportError("connection reset")], answer=[file_answer()]
    )

    answer = client(transport).file_metadata(FILE_HANDLE)

    assert answer["s"] == 5_800_000_000
    assert transport.attempts == 2


def test_a_persistent_transport_failure_is_reported() -> None:
    transport = FailingTransport([ProviderTransportError("down")] * 5)

    with pytest.raises(ProviderTransportError, match="down"):
        client(transport).file_metadata(FILE_HANDLE)

    assert transport.attempts == 3


@pytest.mark.parametrize(
    "answer",
    ["a string", {"s": 1}, [], [{"s": 1}, {"s": 2}], [["nested"]], [None], 0, 7],
)
def test_an_unexpected_shape_is_a_protocol_error(answer: object) -> None:
    transport = RecordingTransport([answer])

    with pytest.raises(ProviderProtocolError):
        client(transport).file_metadata(FILE_HANDLE)


def test_a_non_negative_number_is_reported_as_unexpected() -> None:
    transport = RecordingTransport([[0]])

    with pytest.raises(ProviderProtocolError, match="unexpected numeric answer"):
        client(transport).file_metadata(FILE_HANDLE)


def test_the_client_uses_a_default_retrier() -> None:
    transport = RecordingTransport([[file_answer()]])

    assert MegaApiClient(transport).file_metadata(FILE_HANDLE)["s"] == 5_800_000_000
