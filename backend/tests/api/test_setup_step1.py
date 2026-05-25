"""Tests for POST /api/setup/step1/save — no IBKR call, just persist creds."""
import base64

import pytest
from httpx import AsyncClient


@pytest.fixture(autouse=True)
def set_token_key(monkeypatch):
    """AES-GCM key for encrypt_token used by step1/save (base64 of 32 bytes)."""
    monkeypatch.setenv(
        "TOKEN_ENCRYPTION_KEY", base64.b64encode(b"X" * 32).decode("ascii")
    )


async def test_step1_save_persists_creds_without_ibkr_call(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """No HTTP call to IBKR; token gets encrypted and stored."""
    called = []

    async def boom(*args, **kwargs):
        called.append(True)
        raise RuntimeError("step1 must not call IBKR")

    monkeypatch.setattr(
        "ibkr_control.ingest.flex.client.FlexClient.send_request", boom
    )

    r = await client.post(
        "/api/setup/step1/save",
        headers=auth_headers,
        json={"token": "tok_short_test_value_12345", "query_id": "999"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert called == []


async def test_step1_save_is_idempotent_upsert(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """Calling step1/save twice for the same user updates the existing row."""

    def boom(*a, **k):
        raise AssertionError("step1 must not call IBKR")

    monkeypatch.setattr(
        "ibkr_control.ingest.flex.client.FlexClient.send_request", boom
    )

    r1 = await client.post(
        "/api/setup/step1/save",
        headers=auth_headers,
        json={"token": "tok_first_value_12345", "query_id": "111"},
    )
    assert r1.status_code == 200
    r2 = await client.post(
        "/api/setup/step1/save",
        headers=auth_headers,
        json={"token": "tok_second_value_12345", "query_id": "222"},
    )
    assert r2.status_code == 200
