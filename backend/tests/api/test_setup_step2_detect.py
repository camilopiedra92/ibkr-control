"""Tests for POST /api/setup/step2/detect — mock FlexClient end-to-end.

Covers happy path (F-shadow filtered, accounts returned with metadata),
the retry loop on 1001 busy responses, retry exhaustion, auth errors and
the no-creds short-circuit. The `set_token_key` autouse fixture is
inherited from tests/api/conftest.py.
"""
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from ibkr_control.ingest.flex import client as flex_client_mod


# Minimal valid Activity XML: two AccountInformation rows — one regular (U99999999),
# one F-shadow (U99999999F). The persister/endpoint must surface only the regular
# one. Dates use YYYYMMDD; period_to.year = 2026.
_FAKE_XML = b"""<?xml version="1.0"?>
<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement accountId="U99999999" fromDate="20260101" toDate="20260524">
      <AccountInformation accountId="U99999999" accountAlias="Main" accountType="Individual" name="TEST USER" currency="USD"/>
      <AccountInformation accountId="U99999999F" currency="USD"/>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
"""


async def _seed_creds(client: AsyncClient, auth_headers: dict) -> None:
    """Step1/save must run first so the endpoint has creds to decrypt."""
    r = await client.post(
        "/api/setup/step1/save",
        headers=auth_headers,
        json={"token": "tok_value_12345", "query_id": "999"},
    )
    assert r.status_code == 200, r.text


async def test_step2_detect_happy_path_returns_accounts_filtering_f(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """Single send_request + get_statement → parsed accounts, F filtered out."""
    await _seed_creds(client, auth_headers)

    monkeypatch.setattr(
        flex_client_mod.FlexClient,
        "send_request",
        AsyncMock(return_value="ref-001"),
    )
    monkeypatch.setattr(
        flex_client_mod.FlexClient,
        "get_statement",
        AsyncMock(return_value=_FAKE_XML),
    )

    r = await client.post("/api/setup/step2/detect", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [a["ibkr_account_id"] for a in body["detected_accounts"]]
    assert ids == ["U99999999"], f"F filtered out; got {ids}"
    assert body["detected_accounts"][0]["suggested_alias"] == "Main"
    assert body["detected_accounts"][0]["account_type"] == "Individual"
    assert body["flex_import_id"] > 0


async def test_step2_detect_retries_on_1001_then_succeeds(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """Two 1001 busy responses then success → send_request awaited 3 times."""
    await _seed_creds(client, auth_headers)

    busy = flex_client_mod.FlexBusyError("1001")
    send_mock = AsyncMock(side_effect=[busy, busy, "ref-ok"])
    monkeypatch.setattr(flex_client_mod.FlexClient, "send_request", send_mock)
    monkeypatch.setattr(
        flex_client_mod.FlexClient,
        "get_statement",
        AsyncMock(return_value=_FAKE_XML),
    )
    # Zero out the retry delays so this test doesn't actually sleep [5,15,30]s.
    monkeypatch.setattr(
        "ibkr_control.api.setup._DETECT_RETRY_DELAYS", [0, 0, 0]
    )

    r = await client.post("/api/setup/step2/detect", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert send_mock.await_count == 3


async def test_step2_detect_503_after_max_retries(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """Four consecutive 1001s → 503 IBKR_BUSY with attempts in detail."""
    await _seed_creds(client, auth_headers)
    busy = flex_client_mod.FlexBusyError("1001")
    monkeypatch.setattr(
        flex_client_mod.FlexClient,
        "send_request",
        AsyncMock(side_effect=busy),
    )
    monkeypatch.setattr(
        "ibkr_control.api.setup._DETECT_RETRY_DELAYS", [0, 0, 0]
    )

    r = await client.post("/api/setup/step2/detect", headers=auth_headers)
    assert r.status_code == 503
    body = r.json()
    assert body["detail"]["code"] == "IBKR_BUSY"
    assert body["detail"]["attempts"] == 4


async def test_step2_detect_401_on_invalid_token(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """FlexAuthError → 401 INVALID_TOKEN."""
    await _seed_creds(client, auth_headers)
    monkeypatch.setattr(
        flex_client_mod.FlexClient,
        "send_request",
        AsyncMock(side_effect=flex_client_mod.FlexAuthError("1003", "bad token")),
    )
    r = await client.post("/api/setup/step2/detect", headers=auth_headers)
    assert r.status_code == 401
    assert r.json()["detail"] == "INVALID_TOKEN"


async def test_step2_detect_400_when_no_creds(
    client: AsyncClient, auth_headers: dict
):
    """No FlexCredentials row → 400 MISSING_CREDENTIALS (short-circuit before IBKR)."""
    r = await client.post("/api/setup/step2/detect", headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["detail"] == "MISSING_CREDENTIALS"
