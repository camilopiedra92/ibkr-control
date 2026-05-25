"""Shared fixtures for backend API tests (under tests/api/)."""
import base64

import pytest


@pytest.fixture(autouse=True)
def set_token_key(monkeypatch):
    """AES-GCM key for encrypt_token/decrypt_token (base64 of 32 bytes).

    Autouse so any test that touches an endpoint which encrypts or decrypts
    a Flex token has TOKEN_ENCRYPTION_KEY available without per-test setup.
    Lives in tests/api/conftest.py to be inherited by every test under api/.
    """
    monkeypatch.setenv(
        "TOKEN_ENCRYPTION_KEY", base64.b64encode(b"X" * 32).decode("ascii")
    )
