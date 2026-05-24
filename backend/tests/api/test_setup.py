"""Tests del wizard endpoints /api/setup/*."""
import base64

import pytest
import respx
from httpx import AsyncClient, Response


@pytest.fixture(autouse=True)
def set_token_key(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", base64.b64encode(b"X" * 32).decode("ascii"))


async def _register_and_login(client: AsyncClient, email: str = "setup@test.com") -> str:
    await client.post(
        "/api/auth/register",
        json={"email": email, "password": "supersecret123", "name": "Setup User"},
    )
    login = await client.post(
        "/api/auth/jwt/login",
        data={"username": email, "password": "supersecret123"},
    )
    return login.json()["access_token"]


async def test_get_setup_state_initial(client: AsyncClient):
    token = await _register_and_login(client, "state@test.com")
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get("/api/setup/state", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["step1_credentials"] is False
    assert body["step2_accounts"] is False
    assert body["step3_xmls"] is False
    assert body["setup_completed_at"] is None


async def test_step1_validate_with_valid_token(client: AsyncClient):
    token = await _register_and_login(client, "step1ok@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as mock_router:
        mock_router.get("/Universal/servlet/FlexStatementService.SendRequest").mock(
            return_value=Response(
                200,
                content=b"""<?xml version="1.0"?>
<FlexStatementResponse><Status>Success</Status><ReferenceCode>9999</ReferenceCode></FlexStatementResponse>""",
            )
        )
        resp = await client.post(
            "/api/setup/step1/validate",
            json={"token": "good-token-abc", "query_id": "1234567"},
            headers=headers,
        )

    assert resp.status_code == 200
    state = (await client.get("/api/setup/state", headers=headers)).json()
    assert state["step1_credentials"] is True


async def test_step1_validate_rejects_bad_token(client: AsyncClient):
    token = await _register_and_login(client, "step1bad@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as mock_router:
        mock_router.get("/Universal/servlet/FlexStatementService.SendRequest").mock(
            return_value=Response(
                200,
                content=b"""<?xml version="1.0"?>
<FlexStatementResponse><Status>Fail</Status><ErrorCode>1018</ErrorCode><ErrorMessage>Invalid token</ErrorMessage></FlexStatementResponse>""",
            )
        )
        resp = await client.post(
            "/api/setup/step1/validate",
            json={"token": "bad-token-00000", "query_id": "1"},
            headers=headers,
        )

    assert resp.status_code == 401
    state = (await client.get("/api/setup/state", headers=headers)).json()
    assert state["step1_credentials"] is False


async def test_step2_save_creates_accounts_and_participations(client: AsyncClient):
    token = await _register_and_login(client, "step2@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/setup/step2/save",
        json={
            "accounts": [
                {"ibkr_account_id": "U99999001", "alias": "Joint", "pct": "0.5000"},
                {"ibkr_account_id": "U99999002", "alias": "Swing", "pct": "1.0000"},
                {"ibkr_account_id": "U99999003", "alias": "FUT", "pct": "1.0000"},
            ]
        },
        headers=headers,
    )
    assert resp.status_code == 200

    state = (await client.get("/api/setup/state", headers=headers)).json()
    assert state["step2_accounts"] is True


async def test_step2_rejects_invalid_account_id(client: AsyncClient):
    token = await _register_and_login(client, "step2bad@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/api/setup/step2/save",
        json={
            "accounts": [{"ibkr_account_id": "NOT-VALID", "alias": "x", "pct": "0.5"}]
        },
        headers=headers,
    )
    assert resp.status_code == 422


async def test_step3_complete_marks_progress(client: AsyncClient):
    token = await _register_and_login(client, "step3@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post("/api/setup/step3/complete", headers=headers)
    assert resp.status_code == 200
    state = (await client.get("/api/setup/state", headers=headers)).json()
    assert state["step3_xmls"] is True
