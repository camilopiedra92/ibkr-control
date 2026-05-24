"""Tests del router /api/credentials/flex."""
import base64

import respx
from httpx import AsyncClient, Response


def _make_test_key() -> str:
    return base64.b64encode(b"X" * 32).decode("ascii")


async def _register_and_login(client: AsyncClient) -> str:
    await client.post(
        "/api/auth/register",
        json={"email": "creds@test.com", "password": "supersecret123", "name": "Creds User"},
    )
    login = await client.post(
        "/api/auth/jwt/login",
        data={"username": "creds@test.com", "password": "supersecret123"},
    )
    return login.json()["access_token"]


async def test_get_credentials_404_when_not_configured(client: AsyncClient):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get("/api/credentials/flex", headers=headers)
    assert resp.status_code == 404


async def test_put_credentials_validates_token_with_ibkr(client: AsyncClient, monkeypatch):
    """PUT con token + query_id hace ping a IBKR antes de guardar."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _make_test_key())

    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as mock_router:
        mock_router.get("/Universal/servlet/FlexStatementService.SendRequest").mock(
            return_value=Response(
                200,
                content=b"""<?xml version="1.0"?>
<FlexStatementResponse><Status>Success</Status><ReferenceCode>123</ReferenceCode></FlexStatementResponse>""",
            )
        )
        resp = await client.put(
            "/api/credentials/flex",
            json={"token": "valid-token-abc123", "query_id": "1234567"},
            headers=headers,
        )

    assert resp.status_code == 200

    # GET ahora debe devolver los credentials (sin el token plaintext)
    get_resp = await client.get("/api/credentials/flex", headers=headers)
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["query_id"] == "1234567"
    assert "token" not in body


async def test_put_credentials_rejects_invalid_token(client: AsyncClient, monkeypatch):
    """PUT con token invalido (IBKR retorna ErrorCode 1018) devuelve 401."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _make_test_key())

    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as mock_router:
        mock_router.get("/Universal/servlet/FlexStatementService.SendRequest").mock(
            return_value=Response(
                200,
                content=b"""<?xml version="1.0"?>
<FlexStatementResponse><Status>Fail</Status><ErrorCode>1018</ErrorCode><ErrorMessage>Invalid token</ErrorMessage></FlexStatementResponse>""",
            )
        )
        resp = await client.put(
            "/api/credentials/flex",
            json={"token": "bad-token-123456", "query_id": "1234567"},
            headers=headers,
        )

    assert resp.status_code == 401
    detail = resp.json()["detail"].lower()
    assert "invalid" in detail or "token" in detail


async def test_get_credentials_requires_auth(client: AsyncClient):
    resp = await client.get("/api/credentials/flex")
    assert resp.status_code == 401
