"""Builders for Mega API fixtures.

The payloads are produced here rather than recorded from a live share, so the
repository carries no third-party content and every ciphertext round-trips
against the very functions under test.
"""

import base64
import json
from collections import deque
from collections.abc import Generator, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from maxicrawler.domain import UrlCategory, UrlClassification, UrlRecord
from maxicrawler.providers.mega.crypto import ATTRIBUTE_PREFIX, ZERO_IV

FILE_HANDLE = "AaBbCcDd"
"""Handle of the single-file share used across the tests."""

FILE_AES_KEY = bytes(range(16))
FILE_NONCE = bytes.fromhex("0011223344556677")
FILE_META_MAC = bytes.fromhex("8899aabbccddeeff")

SHARE_HANDLE = "FolderAA"
"""Handle of the folder share used across the tests."""

SHARE_KEY = bytes(range(16, 32))

CHILD_FILE_HANDLE = "FileAAA1"
CHILD_FOLDER_HANDLE = "SubFldr1"
NESTED_FILE_HANDLE = "FileAAA2"

CHILD_FILE_AES_KEY = bytes(range(32, 48))
CHILD_FILE_NONCE = bytes.fromhex("1122334455667788")

NESTED_FILE_AES_KEY = bytes(range(64, 80))
NESTED_FILE_NONCE = bytes.fromhex("2233445566778899")

CHILD_FOLDER_KEY = bytes(range(48, 64))
"""A folder node publishes its 16-byte share key directly."""

UBUNTU_SIZE = 5_800_000_000
NESTED_SIZE = 1_048_576
TIMESTAMP = 1_772_000_000

TRANSFER_URL = "https://gfs001.userstorage.mega.co.nz/dl/AaBbCcDd"
"""The kind of host Mega hands out for a transfer; never the API endpoint."""


def encode_base64(raw: bytes) -> str:
    """Return *raw* in Mega's unpadded base64url encoding."""
    return base64.b64encode(raw, altchars=b"-_").decode("ascii").rstrip("=")


def _encrypt(key: bytes, iv: bytes | None, data: bytes) -> bytes:
    """Return *data* encrypted with AES-128 under *key*."""
    mode = modes.ECB() if iv is None else modes.CBC(iv)
    encryptor = Cipher(algorithms.AES(key), mode).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def pack_file_key(
    aes_key: bytes = FILE_AES_KEY,
    nonce: bytes = FILE_NONCE,
    meta_mac: bytes = FILE_META_MAC,
) -> bytes:
    """Return the 32-byte container Mega publishes for a file key."""
    tail = nonce + meta_mac
    head = bytes(left ^ right for left, right in zip(aes_key, tail, strict=True))
    return head + tail


CHILD_FILE_KEY = pack_file_key(CHILD_FILE_AES_KEY, CHILD_FILE_NONCE)
"""The packed 32-byte key a file node inside a shared folder publishes."""

NESTED_FILE_KEY = pack_file_key(NESTED_FILE_AES_KEY, NESTED_FILE_NONCE)
"""The packed key of the file one level further down."""


def node_attribute_key(node_key: bytes) -> bytes:
    """Return the AES key a node's attributes are encrypted under.

    A folder node uses its 16-byte share key as-is; a file node carries a
    packed 32-byte container whose halves XOR into the AES key. Mirroring that
    here keeps the fixtures honest: a listing they produce decrypts through the
    production code path rather than a simplified one.
    """
    if len(node_key) == 16:
        return node_key
    return bytes(left ^ right for left, right in zip(node_key[:16], node_key[16:], strict=True))


def encrypt_attributes(key: bytes, name: str, **extra: Any) -> str:
    """Return the encrypted attribute block naming *name*."""
    document = json.dumps({"n": name, **extra}, separators=(",", ":")).encode("utf-8")
    block = ATTRIBUTE_PREFIX + document
    padded = block + bytes(-len(block) % 16)
    return encode_base64(_encrypt(key, ZERO_IV, padded))


def encrypt_node_key(share_key: bytes, node_key: bytes) -> str:
    """Return *node_key* encrypted for a holder of *share_key*."""
    return encode_base64(_encrypt(share_key, None, node_key))


def file_answer(
    size: int = UBUNTU_SIZE, name: str = "ubuntu.iso", key: bytes = FILE_AES_KEY
) -> dict[str, Any]:
    """Return the answer Mega gives for a public file link."""
    return {"s": size, "at": encrypt_attributes(key, name)}


def encrypt_content(
    plaintext: bytes, *, key: bytes = FILE_AES_KEY, nonce: bytes = FILE_NONCE
) -> bytes:
    """Return *plaintext* encrypted the way Mega encrypts file content.

    AES-128 in counter mode, starting from the nonce padded with zeros, which
    is what a transfer of a whole file looks like on the wire.
    """
    encryptor = Cipher(algorithms.AES(key), modes.CTR(nonce + bytes(8))).encryptor()
    return encryptor.update(plaintext) + encryptor.finalize()


def transfer_answer(
    size: int,
    *,
    name: str = "ubuntu.iso",
    key: bytes = FILE_AES_KEY,
    url: str = TRANSFER_URL,
) -> dict[str, Any]:
    """Return the answer Mega gives when it allocates a transfer."""
    return {"s": size, "at": encrypt_attributes(key, name), "g": url}


def file_url(handle: str = FILE_HANDLE, *, key: bytes | None = None) -> str:
    """Return a modern file share URL, with the packed key unless suppressed."""
    if key is None:
        return f"https://mega.nz/file/{handle}"
    return f"https://mega.nz/file/{handle}#{encode_base64(pack_file_key(key))}"


def folder_url(handle: str = SHARE_HANDLE, *, key: bytes | None = SHARE_KEY) -> str:
    """Return a modern folder share URL."""
    if key is None:
        return f"https://mega.nz/folder/{handle}"
    return f"https://mega.nz/folder/{handle}#{encode_base64(key)}"


def mega_classification(url: str) -> UrlClassification:
    """Return the classification the Mega plugin would produce for *url*."""
    return UrlClassification(
        record=UrlRecord(raw_url=url, normalized_url=url),
        category=UrlCategory.FILE,
        plugin_name="mega",
    )


def node(
    handle: str,
    *,
    parent: str | None = None,
    node_type: int = 0,
    node_key: bytes | None = None,
    name: str | None = None,
    size: int | None = None,
    timestamp: int | None = TIMESTAMP,
    share_key: bytes = SHARE_KEY,
    share_handle: str = SHARE_HANDLE,
) -> dict[str, Any]:
    """Return one node of a folder listing, encrypted for *share_key*."""
    entry: dict[str, Any] = {"h": handle, "t": node_type}
    if parent is not None:
        entry["p"] = parent
    if size is not None:
        entry["s"] = size
    if timestamp is not None:
        entry["ts"] = timestamp
    if node_key is not None:
        entry["k"] = f"{share_handle}:{encrypt_node_key(share_key, node_key)}"
        if name is not None:
            entry["a"] = encrypt_attributes(node_attribute_key(node_key), name)
    return entry


def folder_nodes(share_key: bytes = SHARE_KEY) -> list[dict[str, Any]]:
    """Return the listing of the folder share used across the tests.

    The tree is a root holding one file and one sub-folder, and that
    sub-folder holds one more file.
    """
    return [
        node(
            SHARE_HANDLE,
            node_type=2,
            node_key=share_key,
            name="Ubuntu Releases",
            share_key=share_key,
        ),
        node(
            CHILD_FILE_HANDLE,
            parent=SHARE_HANDLE,
            node_type=0,
            node_key=CHILD_FILE_KEY,
            name="ubuntu.iso",
            size=UBUNTU_SIZE,
            share_key=share_key,
        ),
        node(
            CHILD_FOLDER_HANDLE,
            parent=SHARE_HANDLE,
            node_type=1,
            node_key=CHILD_FOLDER_KEY,
            name="archive",
            share_key=share_key,
        ),
        node(
            NESTED_FILE_HANDLE,
            parent=CHILD_FOLDER_HANDLE,
            node_type=0,
            node_key=NESTED_FILE_KEY,
            name="checksums.txt",
            size=NESTED_SIZE,
            share_key=share_key,
        ),
    ]


def folder_answer(share_key: bytes = SHARE_KEY) -> dict[str, Any]:
    """Return the answer Mega gives for a folder listing request."""
    return {"f": folder_nodes(share_key)}


@dataclass(frozen=True, slots=True)
class RecordedCall:
    """One request a :class:`RecordingTransport` was asked to make."""

    url: str
    payload: object
    params: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def command(self) -> Mapping[str, Any]:
        """Return the single command in the request body."""
        assert isinstance(self.payload, list) and len(self.payload) == 1
        command = self.payload[0]
        assert isinstance(command, dict)
        return command


class RecordingTransport:
    """Answers with queued documents and records every request verbatim."""

    def __init__(self, answers: Iterable[object] = ()) -> None:
        self._answers = deque(answers)
        self.calls: list[RecordedCall] = []
        self.arguments: dict[str, Any] = {}
        """The keyword arguments the transport was constructed with, if recorded."""

    def queue(self, *answers: object) -> None:
        """Add further answers the transport should hand out."""
        self._answers.extend(answers)

    def post_json(
        self,
        url: str,
        payload: object,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> object:
        """Record the request and return the next queued answer."""
        self.calls.append(
            RecordedCall(
                url=url,
                payload=payload,
                params=dict(params or {}),
                headers=dict(headers or {}),
            )
        )
        if not self._answers:
            msg = "the transport ran out of queued answers"
            raise AssertionError(msg)
        return self._answers.popleft()

    def everything_sent(self) -> str:
        """Return every byte of every request as one searchable string.

        The confinement test scans this for key material, so it must include
        the URL, the query parameters, the headers, and the body.
        """
        return json.dumps(
            [
                {
                    "url": call.url,
                    "params": call.params,
                    "headers": call.headers,
                    "payload": call.payload,
                }
                for call in self.calls
            ],
            default=repr,
        )


class StubStreamTransport:
    """Serves fixed bytes for a transfer and records what was asked for.

    The stream is a generator so a test can observe that an abandoned transfer
    is closed, which is the property the provider relies on to release a
    connection it stopped reading.
    """

    def __init__(self, content: bytes = b"", *, chunk_size: int = 8) -> None:
        self._content = content
        self._chunk_size = max(chunk_size, 1)
        self.urls: list[str] = []
        self.chunk_sizes: list[int] = []
        self.closed = False

    def stream(self, url: str, *, chunk_size: int = 1024) -> Generator[bytes, None, None]:
        """Yield the fixed content, recording the request."""
        self.urls.append(url)
        self.chunk_sizes.append(chunk_size)
        try:
            for start in range(0, len(self._content), self._chunk_size):
                yield self._content[start : start + self._chunk_size]
        finally:
            self.closed = True


class FailingStreamTransport:
    """Raises when a transfer is opened, for failure-path tests."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.urls: list[str] = []

    def stream(self, url: str, *, chunk_size: int = 1024) -> Generator[bytes, None, None]:
        """Record the request and raise the configured error."""
        self.urls.append(url)
        raise self._error
        yield b""  # pragma: no cover - makes this a generator function


class FailingTransport:
    """Raises a queued error for every request, for retry and failure tests."""

    def __init__(self, errors: Sequence[Exception], answer: object = None) -> None:
        self._errors = deque(errors)
        self._answer = answer
        self.attempts = 0

    def post_json(
        self,
        url: str,
        payload: object,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> object:
        """Raise the next queued error, or return the fallback answer."""
        self.attempts += 1
        if self._errors:
            raise self._errors.popleft()
        return self._answer
