"""Tests for GET /api/setup/state — derived + stored wizard state.

The state endpoint is the single source of truth for the frontend wizard
to decide which step to show. It mixes derived booleans (computed from
table counts) with stored fields (setup_progress JSONB + setup_completed_at).

The `set_token_key` autouse fixture is inherited from tests/api/conftest.py.
"""
from httpx import AsyncClient


async def test_state_initial_all_false(client: AsyncClient, auth_headers: dict):
    """Fresh user: no creds, no participations, no setup_progress flags."""
    r = await client.get("/api/setup/state", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["step1_credentials"] is False
    assert body["step2_accounts"] is False
    assert body["step3_xmls"] is False
    assert body["step3_n_xmls_uploaded"] == 0
    assert body["setup_completed_at"] is None
    assert body["pending_stash_temp_ids"] == []


async def test_state_step1_credentials_derives_from_table(
    client: AsyncClient, auth_headers: dict
):
    """After step1/save, step1_credentials flips True — derived from flex_credentials row."""
    r = await client.post(
        "/api/setup/step1/save",
        headers=auth_headers,
        json={"token": "tok_12345_state", "query_id": "999"},
    )
    assert r.status_code == 200, r.text

    r = await client.get("/api/setup/state", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["step1_credentials"] is True
