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


@pytest.fixture(autouse=True)
def reset_step3_stash():
    """Reset the module-level Step3Stash singleton between tests.

    The stash lives in process memory and persists across tests in the same
    pytest run. Each test gets a fresh DB (per `app_with_db`) so user_ids
    are re-issued from 1; without this reset, stale entries from previous
    tests would leak into the new test's view (same user_id, different
    intended user).
    """
    from ibkr_control.api import _step3_stash as stash_mod

    stash_mod._singleton = None
    yield
    stash_mod._singleton = None
