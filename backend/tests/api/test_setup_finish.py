"""Tests for POST /api/setup/finish — preconditions + idempotence.

`finish` is the wizard's terminal step. It demands:
  - at least one Participation row (step2/save ran), AND
  - the user.setup_progress["step3_xmls"] flag set (step3/commit was called,
    even with an empty temp_ids list to explicitly skip historicos).

When both hold, `setup_completed_at` is stamped and subsequent calls
short-circuit with already_completed=True.

The `set_token_key` autouse fixture is inherited from tests/api/conftest.py.
"""
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from ibkr_control.ingest.flex import client as flex_client_mod


# Activity XML used to seed step2/detect — one non-F account.
_FAKE_XML = b"""<?xml version="1.0"?>
<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement accountId="U99999999" fromDate="20260101" toDate="20260524">
      <AccountInformation accountId="U99999999" accountAlias="A" accountType="Joint" name="X" currency="USD"/>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
"""


async def _run_through_to_step3(
    client: AsyncClient, auth_headers: dict, monkeypatch
) -> None:
    """Walk through step1/save -> step2/detect -> step2/save -> step3/commit(empty).

    After this helper returns, the user has creds + an Account + a
    Participation + the step3_xmls flag set, so /finish should succeed.
    """
    monkeypatch.setattr(
        "ibkr_control.api.setup._trm_backfill_background", AsyncMock()
    )
    r = await client.post(
        "/api/setup/step1/save",
        headers=auth_headers,
        json={"token": "tok_value_x_12345", "query_id": "999"},
    )
    assert r.status_code == 200, r.text
    monkeypatch.setattr(
        flex_client_mod.FlexClient, "send_request", AsyncMock(return_value="ref")
    )
    monkeypatch.setattr(
        flex_client_mod.FlexClient,
        "get_statement",
        AsyncMock(return_value=_FAKE_XML),
    )
    r = await client.post("/api/setup/step2/detect", headers=auth_headers)
    assert r.status_code == 200, r.text
    r = await client.post(
        "/api/setup/step2/save",
        headers=auth_headers,
        json={
            "accounts": [
                {"ibkr_account_id": "U99999999", "alias": "A", "pct": "1.0000"}
            ]
        },
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        "/api/setup/step3/commit",
        headers=auth_headers,
        json={"temp_ids": []},
    )
    assert r.status_code == 200, r.text


async def test_finish_400_when_no_participations(
    client: AsyncClient, auth_headers: dict
):
    """Pristine user, no participations yet → 400 INCOMPLETE_SETUP."""
    r = await client.post("/api/setup/finish", headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["detail"] == "INCOMPLETE_SETUP"


async def test_finish_happy_path(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """Full wizard walk-through then /finish → setup_completed_at populated."""
    await _run_through_to_step3(client, auth_headers, monkeypatch)
    r = await client.post("/api/setup/finish", headers=auth_headers)
    assert r.status_code == 200, r.text
    state = await client.get("/api/setup/state", headers=auth_headers)
    assert state.status_code == 200
    assert state.json()["setup_completed_at"] is not None


async def test_finish_idempotent(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """Calling /finish a second time short-circuits with already_completed=True."""
    await _run_through_to_step3(client, auth_headers, monkeypatch)
    r1 = await client.post("/api/setup/finish", headers=auth_headers)
    assert r1.status_code == 200, r1.text
    r2 = await client.post("/api/setup/finish", headers=auth_headers)
    assert r2.status_code == 200, r2.text
    assert r2.json().get("already_completed") is True
