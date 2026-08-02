"""Tests for the local decryption of Mega metadata."""

import pytest
from mega_fixtures import (
    CHILD_FILE_KEY,
    FILE_AES_KEY,
    FILE_META_MAC,
    FILE_NONCE,
    SHARE_HANDLE,
    SHARE_KEY,
    encode_base64,
    encrypt_attributes,
    encrypt_node_key,
    pack_file_key,
)

from maxicrawler.providers import CryptographyCipherBackend, ProviderCryptoError
from maxicrawler.providers.mega.crypto import (
    FILE_KEY_SIZE,
    FOLDER_KEY_SIZE,
    MegaFileKey,
    attribute_name,
    decode_base64,
    decrypt_attributes,
    decrypt_node_key,
    node_aes_key,
    select_key_ciphertext,
    unpack_file_key,
    unpack_folder_key,
)


def cipher() -> CryptographyCipherBackend:
    """Return the real AES backend."""
    return CryptographyCipherBackend()


def test_base64_round_trips_without_padding() -> None:
    raw = bytes(range(32))

    encoded = encode_base64(raw)

    assert "=" not in encoded
    assert decode_base64(encoded) == raw


def test_base64_accepts_the_url_safe_alphabet() -> None:
    raw = bytes.fromhex("fbff")

    assert "-" in encode_base64(raw) or "_" in encode_base64(raw)
    assert decode_base64(encode_base64(raw)) == raw


@pytest.mark.parametrize("value", ["not base64!", "a b c", "***"])
def test_base64_rejects_invalid_input(value: str) -> None:
    with pytest.raises(ProviderCryptoError, match="not valid base64url"):
        decode_base64(value)


def test_base64_error_does_not_repeat_the_offending_text() -> None:
    with pytest.raises(ProviderCryptoError) as failure:
        decode_base64("secret material!!")

    assert "secret material" not in str(failure.value)


def test_file_key_unpacks_into_its_three_parts() -> None:
    unpacked = unpack_file_key(pack_file_key())

    assert unpacked.aes_key == FILE_AES_KEY
    assert unpacked.nonce == FILE_NONCE
    assert unpacked.meta_mac == FILE_META_MAC


def test_file_key_is_the_xor_of_both_halves() -> None:
    raw = bytes(range(FILE_KEY_SIZE))

    unpacked = unpack_file_key(raw)

    assert unpacked.aes_key == bytes(a ^ b for a, b in zip(raw[:16], raw[16:], strict=True))


@pytest.mark.parametrize("length", [0, 16, 31, 33])
def test_file_key_rejects_a_wrong_length(length: int) -> None:
    with pytest.raises(ProviderCryptoError, match="must be 32 bytes"):
        unpack_file_key(bytes(length))


def test_file_key_hides_its_material_from_repr() -> None:
    key = unpack_file_key(pack_file_key())

    assert repr(key) == "MegaFileKey(<redacted>)"
    assert FILE_AES_KEY.hex() not in repr(key)
    assert isinstance(key, MegaFileKey)


def test_folder_key_is_used_as_is() -> None:
    assert unpack_folder_key(SHARE_KEY) == SHARE_KEY


@pytest.mark.parametrize("length", [0, 15, 17, 32])
def test_folder_key_rejects_a_wrong_length(length: int) -> None:
    with pytest.raises(ProviderCryptoError, match="must be 16 bytes"):
        unpack_folder_key(bytes(length))


def test_node_aes_key_unpacks_a_file_key_and_passes_a_folder_key_through() -> None:
    assert node_aes_key(pack_file_key()) == FILE_AES_KEY
    assert node_aes_key(SHARE_KEY) == SHARE_KEY


def test_key_selection_prefers_the_share_holder() -> None:
    encoded = f"OtherOwn:AAAA/{SHARE_HANDLE}:BBBB"

    assert select_key_ciphertext(encoded, SHARE_HANDLE) == "BBBB"


def test_key_selection_accepts_a_single_unambiguous_entry() -> None:
    assert select_key_ciphertext("SomeOwner:BBBB", SHARE_HANDLE) == "BBBB"


def test_key_selection_declines_several_foreign_holders() -> None:
    encoded = "OwnerOne:AAAA/OwnerTwo:BBBB"

    assert select_key_ciphertext(encoded, SHARE_HANDLE) is None


def test_key_selection_declines_a_malformed_field() -> None:
    assert select_key_ciphertext("no-colon-here", SHARE_HANDLE) is None
    assert select_key_ciphertext("", SHARE_HANDLE) is None
    assert select_key_ciphertext(f"{SHARE_HANDLE}:", SHARE_HANDLE) is None


def test_node_key_decrypts_under_the_share_key() -> None:
    ciphertext = encrypt_node_key(SHARE_KEY, CHILD_FILE_KEY)

    assert decrypt_node_key(cipher(), SHARE_KEY, ciphertext) == CHILD_FILE_KEY


def test_node_key_decrypts_a_packed_file_key() -> None:
    packed = pack_file_key()
    ciphertext = encrypt_node_key(SHARE_KEY, packed)

    assert decrypt_node_key(cipher(), SHARE_KEY, ciphertext) == packed


@pytest.mark.parametrize("length", [16 + 8, 48])
def test_node_key_rejects_an_implausible_length(length: int) -> None:
    ciphertext = encode_base64(bytes(length))

    with pytest.raises(ProviderCryptoError, match="must be 16 or 32 bytes"):
        decrypt_node_key(cipher(), SHARE_KEY, ciphertext)


def test_attributes_decrypt_to_the_name() -> None:
    payload = encrypt_attributes(FILE_AES_KEY, "ubuntu.iso")

    attributes = decrypt_attributes(cipher(), FILE_AES_KEY, payload)

    assert attribute_name(attributes) == "ubuntu.iso"


def test_attributes_keep_further_members() -> None:
    payload = encrypt_attributes(FILE_AES_KEY, "ubuntu.iso", c="checksum")

    attributes = decrypt_attributes(cipher(), FILE_AES_KEY, payload)

    assert attributes["c"] == "checksum"


def test_attributes_survive_a_name_needing_several_blocks() -> None:
    name = "a-very-long-release-name-" * 4 + ".iso"
    payload = encrypt_attributes(FILE_AES_KEY, name)

    assert attribute_name(decrypt_attributes(cipher(), FILE_AES_KEY, payload)) == name


def test_attributes_handle_non_ascii_names() -> None:
    payload = encrypt_attributes(FILE_AES_KEY, "Prüfsummen — 2026.txt")

    assert attribute_name(decrypt_attributes(cipher(), FILE_AES_KEY, payload)) == (
        "Prüfsummen — 2026.txt"
    )


def test_attributes_reject_a_wrong_key() -> None:
    payload = encrypt_attributes(FILE_AES_KEY, "ubuntu.iso")

    with pytest.raises(ProviderCryptoError, match="do not carry the Mega marker"):
        decrypt_attributes(cipher(), SHARE_KEY, payload)


def test_attributes_reject_a_block_without_json() -> None:
    payload = encode_base64(
        _encrypted_block(b"MEGAnot-json" + bytes(4)),
    )

    with pytest.raises(ProviderCryptoError, match="not a JSON object"):
        decrypt_attributes(cipher(), FILE_AES_KEY, payload)


def test_attributes_reject_a_json_value_that_is_not_an_object() -> None:
    payload = encode_base64(_encrypted_block(b"MEGA[1,2,3]" + bytes(5)))

    with pytest.raises(ProviderCryptoError, match="not a JSON object"):
        decrypt_attributes(cipher(), FILE_AES_KEY, payload)


def test_attribute_name_ignores_a_missing_or_unusable_name() -> None:
    assert attribute_name({}) is None
    assert attribute_name({"n": ""}) is None
    assert attribute_name({"n": 42}) is None
    assert attribute_name({"n": "ok"}) == "ok"


def test_folder_key_size_constants_match_the_link_formats() -> None:
    assert len(encode_base64(bytes(FOLDER_KEY_SIZE))) == 22
    assert len(encode_base64(bytes(FILE_KEY_SIZE))) == 43


def _encrypted_block(plaintext: bytes) -> bytes:
    """Return *plaintext* encrypted the way Mega encrypts attributes."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    from maxicrawler.providers.mega.crypto import ZERO_IV

    padded = plaintext + bytes(-len(plaintext) % 16)
    encryptor = Cipher(algorithms.AES(FILE_AES_KEY), modes.CBC(ZERO_IV)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()
