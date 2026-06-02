"""Tests de AES-GCM para Flex token encryption."""

import base64
import pytest

from cryptography.exceptions import InvalidTag

from ibkr_control.ingest.flex.crypto import decrypt_token, encrypt_token


@pytest.fixture(autouse=True)
def set_key(monkeypatch):
    """Setea una key conocida para todos los tests del módulo."""
    key = base64.b64encode(b"X" * 32).decode("ascii")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)


def test_round_trip_simple():
    plaintext = "abc123xyz"
    blob = encrypt_token(plaintext)
    assert decrypt_token(blob) == plaintext


def test_round_trip_long_token():
    plaintext = "a" * 1024
    blob = encrypt_token(plaintext)
    assert decrypt_token(blob) == plaintext


def test_round_trip_unicode():
    plaintext = "tóken-con-ñ-y-emoji-🔐"
    blob = encrypt_token(plaintext)
    assert decrypt_token(blob) == plaintext


def test_encrypt_produces_different_ciphertexts_for_same_plaintext():
    """Nonce es aleatorio — dos encrypts del mismo plaintext dan ciphertexts distintos."""
    blob1 = encrypt_token("same-input")
    blob2 = encrypt_token("same-input")
    assert blob1 != blob2
    assert decrypt_token(blob1) == decrypt_token(blob2) == "same-input"


def test_decrypt_tampered_blob_raises(monkeypatch):
    """AES-GCM detecta tampering vía el authentication tag."""
    blob = encrypt_token("real-token")
    tampered = bytearray(blob)
    tampered[-1] ^= 0xFF  # flip un bit del tag
    with pytest.raises(InvalidTag):
        decrypt_token(bytes(tampered))


def test_missing_env_var_raises(monkeypatch):
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    with pytest.raises(KeyError):
        encrypt_token("x")


def test_invalid_key_size_raises(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", base64.b64encode(b"X" * 16).decode("ascii"))
    with pytest.raises(RuntimeError, match="32 bytes"):
        encrypt_token("x")


def test_blob_structure_nonce_prepended():
    """Verifica el formato: nonce(12) || ciphertext || tag(16)."""
    blob = encrypt_token("test")
    # nonce + ciphertext (4 bytes "test") + tag (16 bytes) = 12 + 4 + 16 = 32 bytes
    assert len(blob) == 32
