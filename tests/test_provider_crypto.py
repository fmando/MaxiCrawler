"""Tests for the cipher abstraction."""

import pytest

from maxicrawler.providers import (
    CipherBackend,
    CryptographyCipherBackend,
    ProviderCryptoError,
    default_cipher_backend,
)
from maxicrawler.providers.crypto import validate_ciphertext, validate_key

KEY = bytes(range(16))
IV = bytes(16)


def backend() -> CryptographyCipherBackend:
    """Return the real AES backend."""
    return CryptographyCipherBackend()


def test_backend_satisfies_the_runtime_protocol() -> None:
    assert isinstance(backend(), CipherBackend)


def test_default_backend_is_available_with_the_optional_extra() -> None:
    assert isinstance(default_cipher_backend(), CipherBackend)


def test_ecb_decrypts_a_known_vector() -> None:
    # FIPS-197 AES-128 example: key 000102...0f, plaintext 00112233..ff.
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    ciphertext = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")
    plaintext = bytes.fromhex("00112233445566778899aabbccddeeff")

    assert backend().aes_ecb_decrypt(key, ciphertext) == plaintext


def test_cbc_with_a_zero_iv_matches_ecb_for_a_single_block() -> None:
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    ciphertext = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")

    assert backend().aes_cbc_decrypt(key, IV, ciphertext) == backend().aes_ecb_decrypt(
        key, ciphertext
    )


def test_cbc_chains_across_blocks() -> None:
    cipher = backend()
    ciphertext = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a" * 2)

    decrypted = cipher.aes_cbc_decrypt(KEY, IV, ciphertext)

    assert len(decrypted) == 32
    assert decrypted[:16] != decrypted[16:]


@pytest.mark.parametrize("key", [b"", bytes(15), bytes(32)])
def test_backend_rejects_a_key_of_the_wrong_length(key: bytes) -> None:
    with pytest.raises(ProviderCryptoError, match="AES key must be 16 bytes"):
        backend().aes_ecb_decrypt(key, bytes(16))


@pytest.mark.parametrize("data", [b"", bytes(15), bytes(17)])
def test_backend_rejects_misaligned_ciphertext(data: bytes) -> None:
    with pytest.raises(ProviderCryptoError):
        backend().aes_ecb_decrypt(KEY, data)


def test_backend_rejects_an_initialization_vector_of_the_wrong_length() -> None:
    with pytest.raises(ProviderCryptoError, match="initialization vector must be 16 bytes"):
        backend().aes_cbc_decrypt(KEY, bytes(8), bytes(16))


def test_validation_errors_never_repeat_the_key_material() -> None:
    key = bytes.fromhex("dead") * 4

    with pytest.raises(ProviderCryptoError) as failure:
        validate_key(key + b"\x00")

    assert "dead" not in str(failure.value)
    assert key.hex() not in str(failure.value)


def test_validate_key_accepts_an_aes_128_key() -> None:
    assert validate_key(KEY) is None


def test_validate_ciphertext_accepts_an_aligned_block() -> None:
    assert validate_ciphertext(bytes(32)) is None


def test_validate_ciphertext_rejects_an_empty_payload() -> None:
    with pytest.raises(ProviderCryptoError, match="must not be empty"):
        validate_ciphertext(b"")
