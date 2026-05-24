"""AES-GCM encrypt/decrypt para el Flex Token.

Formato del blob: nonce(12) || ciphertext(N) || tag(16)
Key: env var TOKEN_ENCRYPTION_KEY, base64 de 32 bytes raw.
"""
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _key() -> bytes:
    raw = os.environ["TOKEN_ENCRYPTION_KEY"]
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError(
            f"TOKEN_ENCRYPTION_KEY must decode to exactly 32 bytes, got {len(key)}"
        )
    return key


def encrypt_token(plaintext: str) -> bytes:
    aes = AESGCM(_key())
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return nonce + ct


def decrypt_token(blob: bytes) -> str:
    aes = AESGCM(_key())
    nonce, ct = blob[:12], blob[12:]
    return aes.decrypt(nonce, ct, associated_data=None).decode("utf-8")
