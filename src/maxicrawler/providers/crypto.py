"""The single seam through which MaxiCrawler performs decryption.

Providers depend on :class:`CipherBackend` rather than on a concrete crypto
library. That keeps the optional dependency optional — a provider whose links
carry no credential never needs it — and it puts every cipher call in one
auditable place.

No function here ever sees a URL, a host, or a socket: decryption is local by
construction, which is what makes a Mega share link safe to hand around.
"""

from typing import Protocol, runtime_checkable

from maxicrawler.providers.errors import ProviderCryptoError, ProviderDependencyError

AES_BLOCK_SIZE = 16
"""Block size of AES in bytes."""

AES_KEY_SIZE = 16
"""Key size MaxiCrawler uses; Mega node keys are AES-128."""

INSTALL_HINT = "install it with: pip install 'maxicrawler[mega]'"
"""How to obtain the optional decryption dependency."""


@runtime_checkable
class CipherBackend(Protocol):
    """Decrypts AES-128 ciphertext.

    Only decryption is exposed: MaxiCrawler reads shared resources and never
    produces ciphertext of its own.
    """

    def aes_ecb_decrypt(self, key: bytes, data: bytes) -> bytes:
        """Return *data* decrypted with AES-128-ECB under *key*."""
        ...

    def aes_cbc_decrypt(self, key: bytes, iv: bytes, data: bytes) -> bytes:
        """Return *data* decrypted with AES-128-CBC under *key* and *iv*."""
        ...


class CryptographyCipherBackend:
    """AES-128 decryption backed by the optional ``cryptography`` package.

    The import happens during construction rather than at module import time,
    so importing :mod:`maxicrawler.providers` stays possible without the extra
    installed and the failure names the missing package precisely.
    """

    def __init__(self) -> None:
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError as error:  # pragma: no cover - exercised only without the extra
            msg = f"the cryptography package is required to decrypt metadata; {INSTALL_HINT}"
            raise ProviderDependencyError(msg) from error
        self._cipher = Cipher
        self._algorithms = algorithms
        self._modes = modes

    def aes_ecb_decrypt(self, key: bytes, data: bytes) -> bytes:
        """Return *data* decrypted with AES-128-ECB under *key*."""
        validate_key(key)
        validate_ciphertext(data)
        cipher = self._cipher(self._algorithms.AES(key), self._modes.ECB())
        decryptor = cipher.decryptor()
        return decryptor.update(data) + decryptor.finalize()

    def aes_cbc_decrypt(self, key: bytes, iv: bytes, data: bytes) -> bytes:
        """Return *data* decrypted with AES-128-CBC under *key* and *iv*."""
        validate_key(key)
        validate_ciphertext(data)
        if len(iv) != AES_BLOCK_SIZE:
            msg = f"initialization vector must be {AES_BLOCK_SIZE} bytes, got {len(iv)}"
            raise ProviderCryptoError(msg)
        cipher = self._cipher(self._algorithms.AES(key), self._modes.CBC(iv))
        decryptor = cipher.decryptor()
        return decryptor.update(data) + decryptor.finalize()


def validate_key(key: bytes) -> None:
    """Reject a key of the wrong length.

    Raises:
        ProviderCryptoError: *key* is not an AES-128 key. The message reports
            the length only, never the material.
    """
    if len(key) != AES_KEY_SIZE:
        msg = f"AES key must be {AES_KEY_SIZE} bytes, got {len(key)}"
        raise ProviderCryptoError(msg)


def validate_ciphertext(data: bytes) -> None:
    """Reject ciphertext that is empty or not block aligned.

    Raises:
        ProviderCryptoError: *data* cannot be an AES ciphertext.
    """
    if not data:
        msg = "ciphertext must not be empty"
        raise ProviderCryptoError(msg)
    if len(data) % AES_BLOCK_SIZE:
        msg = f"ciphertext must be a multiple of {AES_BLOCK_SIZE} bytes, got {len(data)}"
        raise ProviderCryptoError(msg)


def default_cipher_backend() -> CipherBackend | None:
    """Return the AES backend, or ``None`` when the optional extra is absent.

    Returning ``None`` lets an inspection still report sizes, timestamps, and
    structure for a link whose names it cannot read.
    """
    try:
        return CryptographyCipherBackend()
    except ProviderDependencyError:
        return None
