"""Local decryption of Mega share metadata.

A Mega share link keeps its decryption key in the URL fragment, which no HTTP
client ever transmits. Everything in this module therefore happens on this
machine: nothing here knows a host, a URL, or a socket, and the key material a
caller passes in is never returned, logged, or embedded in an error message.

The layout follows Mega's own clients:

* A file key is 32 bytes. It is an obfuscated container, not a 256-bit key:
  the first sixteen bytes XOR-ed with the last sixteen give the AES-128 key,
  bytes 16..24 are the counter nonce, and bytes 24..32 are the meta-MAC used to
  verify downloaded content.
* A folder key is 16 bytes and is used directly as the share key.
* A node inside a shared folder carries its own key encrypted with AES-ECB
  under the share key.
* Attributes are AES-128-CBC with an all-zero IV, and decrypt to the literal
  ``MEGA`` followed by a JSON object whose ``n`` member is the name.
"""

import base64
import binascii
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from maxicrawler.providers.crypto import AES_BLOCK_SIZE, CipherBackend
from maxicrawler.providers.errors import ProviderCryptoError

FILE_KEY_SIZE = 32
"""Bytes in the key of a Mega file link; 43 base64url characters."""

FOLDER_KEY_SIZE = 16
"""Bytes in the key of a Mega folder link; 22 base64url characters."""

ATTRIBUTE_PREFIX = b"MEGA"
"""Marker that a decrypted attribute block starts with when the key was right."""

ZERO_IV = bytes(AES_BLOCK_SIZE)
"""Mega encrypts attributes in CBC mode with an all-zero initialization vector."""


@dataclass(frozen=True, slots=True, repr=False)
class MegaFileKey:
    """The three parts a 32-byte Mega file key unpacks into.

    The representation is redacted so that a key cannot reach a log record or
    an exception through an accidental ``repr()``.
    """

    aes_key: bytes
    nonce: bytes
    meta_mac: bytes

    def __repr__(self) -> str:
        """Return a redacted representation; no key material appears."""
        return f"{type(self).__name__}(<redacted>)"


def decode_base64(value: str) -> bytes:
    """Return the bytes behind Mega's unpadded base64url encoding.

    Raises:
        ProviderCryptoError: *value* is not base64url. The message does not
            repeat the offending text.
    """
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as error:
        msg = "value is not valid base64url"
        raise ProviderCryptoError(msg) from error


def unpack_file_key(raw: bytes) -> MegaFileKey:
    """Return the AES key, nonce, and meta-MAC packed into a file key.

    Raises:
        ProviderCryptoError: *raw* is not a 32-byte file key.
    """
    if len(raw) != FILE_KEY_SIZE:
        msg = f"Mega file key must be {FILE_KEY_SIZE} bytes, got {len(raw)}"
        raise ProviderCryptoError(msg)
    aes_key = bytes(left ^ right for left, right in zip(raw[:16], raw[16:], strict=True))
    return MegaFileKey(aes_key=aes_key, nonce=raw[16:24], meta_mac=raw[24:32])


def unpack_folder_key(raw: bytes) -> bytes:
    """Return the share key of a folder link, which is used as-is.

    Raises:
        ProviderCryptoError: *raw* is not a 16-byte folder key.
    """
    if len(raw) != FOLDER_KEY_SIZE:
        msg = f"Mega folder key must be {FOLDER_KEY_SIZE} bytes, got {len(raw)}"
        raise ProviderCryptoError(msg)
    return raw


def select_key_ciphertext(encoded: str, share_handle: str) -> str | None:
    """Return the encrypted node key *share_handle* is able to open.

    Mega states node keys as ``<holder>:<ciphertext>`` and may list several
    holders separated by ``/``. Inside a public folder every node is keyed to
    the share itself, so the matching holder is preferred; a single unambiguous
    entry is accepted as well, because older shares name the owner instead.
    """
    parts = [part for part in encoded.split("/") if ":" in part]
    for part in parts:
        holder, _, ciphertext = part.partition(":")
        if holder == share_handle and ciphertext:
            return ciphertext
    if len(parts) == 1:
        return parts[0].partition(":")[2] or None
    return None


def decrypt_node_key(cipher: CipherBackend, share_key: bytes, ciphertext: str) -> bytes:
    """Return the node key hidden in *ciphertext* under *share_key*.

    Raises:
        ProviderCryptoError: the ciphertext is not a plausible node key.
    """
    raw = decode_base64(ciphertext)
    if len(raw) not in {FOLDER_KEY_SIZE, FILE_KEY_SIZE}:
        msg = f"encrypted Mega node key must be 16 or 32 bytes, got {len(raw)}"
        raise ProviderCryptoError(msg)
    return cipher.aes_ecb_decrypt(share_key, raw)


def node_aes_key(raw: bytes) -> bytes:
    """Return the AES key of a decrypted node key, whatever its kind.

    A file node carries a packed 32-byte key; a folder node carries its
    16-byte share key directly.
    """
    if len(raw) == FILE_KEY_SIZE:
        return unpack_file_key(raw).aes_key
    return unpack_folder_key(raw)


def decrypt_attributes(cipher: CipherBackend, key: bytes, payload: str) -> Mapping[str, Any]:
    """Return the attribute object *payload* holds.

    Raises:
        ProviderCryptoError: the payload did not decrypt to a Mega attribute
            block, which is what a wrong or truncated key looks like.
    """
    plaintext = cipher.aes_cbc_decrypt(key, ZERO_IV, decode_base64(payload))
    block = plaintext.rstrip(b"\x00")
    if not block.startswith(ATTRIBUTE_PREFIX):
        msg = "decrypted attributes do not carry the Mega marker"
        raise ProviderCryptoError(msg)
    try:
        document = json.loads(block[len(ATTRIBUTE_PREFIX) :].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        msg = "decrypted attributes are not a JSON object"
        raise ProviderCryptoError(msg) from error
    if not isinstance(document, dict):
        msg = "decrypted attributes are not a JSON object"
        raise ProviderCryptoError(msg)
    return document


def attribute_name(attributes: Mapping[str, Any]) -> str | None:
    """Return the ``n`` member of an attribute block, if it is a string."""
    name = attributes.get("n")
    return name if isinstance(name, str) and name else None
