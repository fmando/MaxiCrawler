"""Tests for transferring Mega content.

The payloads here are encrypted by the fixtures with the very key the provider
is given, so a passing test proves the whole chain — request, stream, counter
mode, chunk boundaries — rather than only that bytes moved.
"""

import pytest
from doubles import RecordingSink, make_ref
from mega_fixtures import (
    CHILD_FILE_AES_KEY,
    CHILD_FILE_HANDLE,
    CHILD_FILE_NONCE,
    FILE_AES_KEY,
    FILE_HANDLE,
    SHARE_HANDLE,
    SHARE_KEY,
    TRANSFER_URL,
    FailingStreamTransport,
    RecordingTransport,
    StubStreamTransport,
    encode_base64,
    encrypt_content,
    file_url,
    folder_answer,
    folder_url,
    mega_classification,
    pack_file_key,
    transfer_answer,
)

from maxicrawler.domain import ProviderCapability, ResourceKind
from maxicrawler.providers import (
    CryptographyCipherBackend,
    MegaApiError,
    ProviderCryptoError,
    ProviderProtocolError,
    ProviderTransportError,
    Retrier,
    RetryPolicy,
    UnsupportedResourceError,
)
from maxicrawler.providers.mega import (
    MegaApiClient,
    MegaProvider,
    counter_block,
    decrypt_content,
    transfer_url,
)
from maxicrawler.providers.mega.crypto import unpack_file_key

PAYLOAD = b"ubuntu release image, in miniature" * 7


def build(
    answers: list[object], content: bytes, *, chunk_size: int = 8
) -> tuple[MegaProvider, RecordingTransport, StubStreamTransport]:
    """Return a provider wired to queued answers and fixed transfer content."""
    api_transport = RecordingTransport(answers)
    stream = StubStreamTransport(content, chunk_size=chunk_size)
    provider = MegaProvider(
        MegaApiClient(api_transport, retrier=Retrier(RetryPolicy(max_attempts=1))),
        cipher=CryptographyCipherBackend(),
        stream=stream,
    )
    return provider, api_transport, stream


def test_a_file_share_is_transferred_and_decrypted() -> None:
    provider, _, stream = build(
        [[transfer_answer(len(PAYLOAD))]], encrypt_content(PAYLOAD), chunk_size=8
    )
    sink = RecordingSink()

    descriptor = provider.download(
        provider.reference(mega_classification(file_url(key=FILE_AES_KEY))), sink
    )

    assert sink.payload == PAYLOAD
    assert descriptor.name == "ubuntu.iso"
    assert descriptor.size == len(PAYLOAD)
    assert stream.urls == [TRANSFER_URL]


def test_the_sink_learns_what_is_coming_before_the_first_chunk() -> None:
    provider, _, _ = build([[transfer_answer(len(PAYLOAD))]], encrypt_content(PAYLOAD))

    class OrderCheckingSink(RecordingSink):
        def write(self, chunk: bytes) -> None:
            assert self.descriptor is not None, "content was written before begin()"
            super().write(chunk)

    sink = OrderCheckingSink()
    provider.download(provider.reference(mega_classification(file_url(key=FILE_AES_KEY))), sink)

    assert sink.descriptor is not None
    assert sink.payload == PAYLOAD


def test_the_transfer_request_sets_the_download_flag() -> None:
    provider, api, _ = build([[transfer_answer(len(PAYLOAD))]], encrypt_content(PAYLOAD))

    provider.download(
        provider.reference(mega_classification(file_url(key=FILE_AES_KEY))), RecordingSink()
    )

    assert api.calls[-1].command == {"a": "g", "g": 1, "p": FILE_HANDLE}


def test_the_stream_is_closed_when_the_transfer_ends() -> None:
    provider, _, stream = build([[transfer_answer(len(PAYLOAD))]], encrypt_content(PAYLOAD))

    provider.download(
        provider.reference(mega_classification(file_url(key=FILE_AES_KEY))), RecordingSink()
    )

    assert stream.closed is True


def test_the_stream_is_closed_when_a_sink_fails_part_way() -> None:
    provider, _, stream = build([[transfer_answer(len(PAYLOAD))]], encrypt_content(PAYLOAD))

    class BrokenSink(RecordingSink):
        def write(self, chunk: bytes) -> None:
            msg = "the disk is full"
            raise OSError(msg)

    with pytest.raises(OSError, match="disk is full"):
        provider.download(
            provider.reference(mega_classification(file_url(key=FILE_AES_KEY))), BrokenSink()
        )

    assert stream.closed is True


def test_an_empty_file_is_transferred_without_any_chunk() -> None:
    provider, _, _ = build([[transfer_answer(0)]], b"")
    sink = RecordingSink()

    descriptor = provider.download(
        provider.reference(mega_classification(file_url(key=FILE_AES_KEY))), sink
    )

    assert sink.payload == b""
    assert descriptor.size == 0
    assert sink.descriptor is not None


def test_an_entry_inside_a_shared_folder_is_transferred() -> None:
    content = encrypt_content(PAYLOAD, key=CHILD_FILE_AES_KEY, nonce=CHILD_FILE_NONCE)
    answer = transfer_answer(len(PAYLOAD), name="ubuntu.iso", key=CHILD_FILE_AES_KEY)
    provider, api, _ = build([[folder_answer()], [answer]], content)
    url = f"{folder_url()}/file/{CHILD_FILE_HANDLE}"
    sink = RecordingSink()

    descriptor = provider.download(provider.reference(mega_classification(url)), sink)

    assert sink.payload == PAYLOAD
    assert descriptor.name == "ubuntu.iso"
    assert api.calls[0].command == {"a": "f", "c": 1, "r": 1}
    assert api.calls[1].command == {"a": "g", "g": 1, "n": CHILD_FILE_HANDLE}
    assert api.calls[1].params["n"] == SHARE_HANDLE


def test_the_key_is_resolved_before_a_transfer_is_allocated() -> None:
    """A share whose key cannot be read must not cost the owner any quota."""
    provider, api, _ = build([[folder_answer()]], b"")
    url = f"{folder_url()}/file/N0Th3r3A"

    with pytest.raises(UnsupportedResourceError, match="no longer lists this entry"):
        provider.download(provider.reference(mega_classification(url)), RecordingSink())

    assert [call.command["a"] for call in api.calls] == ["f"]


def test_a_folder_is_not_a_transfer() -> None:
    provider, api, _ = build([], b"")

    with pytest.raises(UnsupportedResourceError, match="not a transfer"):
        provider.download(provider.reference(mega_classification(folder_url())), RecordingSink())

    assert api.calls == []


def test_a_link_without_a_key_cannot_be_transferred() -> None:
    provider, api, _ = build([], b"")

    with pytest.raises(UnsupportedResourceError, match="no decryption key"):
        provider.download(provider.reference(mega_classification(file_url())), RecordingSink())

    assert api.calls == []


def test_a_foreign_reference_is_refused() -> None:
    provider, _, _ = build([], b"")

    with pytest.raises(UnsupportedResourceError, match="another provider"):
        provider.download(make_ref(provider="gofile", secret="x" * 43), RecordingSink())


def test_a_broken_transfer_is_reported_as_a_transport_failure() -> None:
    api_transport = RecordingTransport([[transfer_answer(len(PAYLOAD))]])
    provider = MegaProvider(
        MegaApiClient(api_transport, retrier=Retrier(RetryPolicy(max_attempts=1))),
        cipher=CryptographyCipherBackend(),
        stream=FailingStreamTransport(ProviderTransportError("connection reset")),
    )

    with pytest.raises(ProviderTransportError, match="connection reset"):
        provider.download(
            provider.reference(mega_classification(file_url(key=FILE_AES_KEY))), RecordingSink()
        )


def test_a_provider_without_a_stream_transport_says_so() -> None:
    provider = MegaProvider(
        MegaApiClient(RecordingTransport([])), cipher=CryptographyCipherBackend()
    )

    assert provider.metadata.supports(ProviderCapability.DOWNLOAD) is False
    with pytest.raises(ProviderTransportError, match="without a transfer transport"):
        provider.download(
            provider.reference(mega_classification(file_url(key=FILE_AES_KEY))), RecordingSink()
        )


def test_a_provider_with_a_stream_transport_advertises_downloading() -> None:
    provider, _, _ = build([], b"")

    assert provider.metadata.supports(ProviderCapability.DOWNLOAD) is True


def test_a_provider_without_a_cipher_does_not_advertise_downloading() -> None:
    provider = MegaProvider(
        MegaApiClient(RecordingTransport([])), cipher=None, stream=StubStreamTransport()
    )

    assert provider.metadata.supports(ProviderCapability.DOWNLOAD) is False


def test_the_configured_chunk_size_reaches_the_transport() -> None:
    api_transport = RecordingTransport([[transfer_answer(len(PAYLOAD))]])
    stream = StubStreamTransport(encrypt_content(PAYLOAD))
    provider = MegaProvider(
        MegaApiClient(api_transport, retrier=Retrier(RetryPolicy(max_attempts=1))),
        cipher=CryptographyCipherBackend(),
        stream=stream,
        chunk_size=4096,
    )

    provider.download(
        provider.reference(mega_classification(file_url(key=FILE_AES_KEY))), RecordingSink()
    )

    assert stream.chunk_sizes == [4096]


def test_an_impossible_chunk_size_is_rejected() -> None:
    with pytest.raises(ValueError, match="chunk_size must be at least 1"):
        MegaProvider(MegaApiClient(RecordingTransport([])), chunk_size=0)


def test_a_transfer_answer_without_a_url_is_a_protocol_error() -> None:
    assert transfer_url({"g": TRANSFER_URL}) == TRANSFER_URL
    assert transfer_url({"g": [TRANSFER_URL]}) == TRANSFER_URL
    for answer in ({}, {"g": 5}, {"g": []}, {"g": "ftp://example.test/x"}):
        with pytest.raises(ProviderProtocolError, match="no usable download URL"):
            transfer_url(answer)


def test_an_embedded_status_code_is_not_read_as_an_empty_file() -> None:
    provider, _, _ = build([[{"e": -17}]], b"")

    with pytest.raises(MegaApiError, match="EOVERQUOTA"):
        provider.download(
            provider.reference(mega_classification(file_url(key=FILE_AES_KEY))), RecordingSink()
        )


def test_the_counter_block_starts_at_the_beginning_of_the_file() -> None:
    nonce = bytes.fromhex("0011223344556677")

    assert counter_block(nonce) == nonce + bytes(8)


def test_a_counter_nonce_of_the_wrong_size_is_rejected() -> None:
    with pytest.raises(ProviderCryptoError, match="counter nonce must be 8 bytes"):
        counter_block(b"short")


@pytest.mark.parametrize("chunk_size", [1, 3, 16, 17, 4096])
def test_decryption_is_independent_of_chunk_boundaries(chunk_size: int) -> None:
    cipher = CryptographyCipherBackend()
    key = unpack_file_key(pack_file_key(FILE_AES_KEY))
    ciphertext = encrypt_content(PAYLOAD)
    chunks = [
        ciphertext[start : start + chunk_size] for start in range(0, len(ciphertext), chunk_size)
    ]

    plaintext = b"".join(decrypt_content(cipher, key, chunks))

    assert plaintext == PAYLOAD


def test_an_empty_chunk_never_reaches_the_sink() -> None:
    cipher = CryptographyCipherBackend()
    key = unpack_file_key(pack_file_key(FILE_AES_KEY))

    blocks = list(decrypt_content(cipher, key, [b"", encrypt_content(PAYLOAD), b""]))

    assert b"" not in blocks
    assert b"".join(blocks) == PAYLOAD


def test_a_transferred_reference_is_the_one_the_link_names() -> None:
    provider, _, _ = build([], b"")

    ref = provider.reference(mega_classification(file_url(key=FILE_AES_KEY)))

    assert ref.kind is ResourceKind.FILE
    assert ref.resource_id == FILE_HANDLE
    assert ref.url == f"https://mega.nz/file/{FILE_HANDLE}"
    assert encode_base64(SHARE_KEY) not in repr(ref)
