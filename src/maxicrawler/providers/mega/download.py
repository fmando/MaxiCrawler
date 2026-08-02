"""Local decryption of Mega file content.

Everything here happens on this machine. Like its sibling
:mod:`maxicrawler.providers.mega.crypto`, this module knows no host, no URL,
and no socket: it is handed an iterable of ciphertext chunks by the provider
and yields plaintext ones back. The key material it receives is never
returned, logged, or embedded in an error message.

Mega encrypts file content with AES-128 in counter mode. The key and the
counter nonce both come from the 32-byte container published in the share
link; the counter block is the eight-byte nonce followed by eight zero bytes,
because a transfer always starts at the beginning of the file. Counter mode
turns the block cipher into a stream cipher, which is why a download can be
decrypted as it arrives and written straight to disk instead of being
assembled in memory first.
"""

from collections.abc import Iterable, Iterator

from maxicrawler.providers.crypto import AES_BLOCK_SIZE, CipherBackend
from maxicrawler.providers.errors import ProviderCryptoError
from maxicrawler.providers.mega.crypto import MegaFileKey

NONCE_SIZE = 8
"""Bytes of the counter nonce packed into a Mega file key."""


def counter_block(nonce: bytes) -> bytes:
    """Return the initial AES-CTR counter block of a Mega file.

    Raises:
        ProviderCryptoError: *nonce* is not the size a Mega file key carries.
    """
    if len(nonce) != NONCE_SIZE:
        msg = f"Mega counter nonce must be {NONCE_SIZE} bytes, got {len(nonce)}"
        raise ProviderCryptoError(msg)
    return nonce + bytes(AES_BLOCK_SIZE - NONCE_SIZE)


def decrypt_content(
    cipher: CipherBackend, key: MegaFileKey, chunks: Iterable[bytes]
) -> Iterator[bytes]:
    """Yield the plaintext of *chunks*, decrypted under *key*.

    Chunk boundaries are whatever the network produced and are deliberately
    not aligned to anything: the stream keeps its position between calls, so a
    caller may pass on exactly what it read.

    Empty chunks are skipped rather than forwarded, so a sink never sees a
    write that carries nothing.
    """
    stream = cipher.aes_ctr_stream(key.aes_key, counter_block(key.nonce))
    for chunk in chunks:
        if not chunk:
            continue
        plaintext = stream.update(chunk)
        if plaintext:
            yield plaintext
