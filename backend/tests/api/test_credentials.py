"""Tests del router /api/credentials/flex."""
import base64

import httpx
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


# ---------------------------------------------------------------------------
# Additional tests to raise coverage on lines 27-29, 47-83
# ---------------------------------------------------------------------------


async def _register_and_login_unique(client: AsyncClient, email: str) -> str:
    """Register a unique user and return JWT token."""
    await client.post(
        "/api/auth/register",
        json={"email": email, "password": "supersecret123", "name": "Test User"},
    )
    login = await client.post(
        "/api/auth/jwt/login",
        data={"username": email, "password": "supersecret123"},
    )
    return login.json()["access_token"]


async def test_get_credentials_returns_metadata_without_token_plaintext(
    client: AsyncClient, monkeypatch
):
    """GET /credentials/flex returns query_id + timestamps, never the token plaintext."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _make_test_key())
    token = await _register_and_login_unique(client, "getmeta@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    success_xml = b"""<?xml version="1.0"?>
<FlexStatementResponse><Status>Success</Status><ReferenceCode>42</ReferenceCode></FlexStatementResponse>"""

    # PUT credentials first so there is something to GET
    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as mock_router:
        mock_router.get("/Universal/servlet/FlexStatementService.SendRequest").mock(
            return_value=Response(200, content=success_xml)
        )
        await client.put(
            "/api/credentials/flex",
            json={"token": "secret-token-abc123", "query_id": "QID-555"},
            headers=headers,
        )

    get_resp = await client.get("/api/credentials/flex", headers=headers)
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["query_id"] == "QID-555"
    # Token plaintext must never be returned
    assert "token" not in body
    assert "secret" not in str(body)
    # configured_at and last_rotated_at must be present (schema requires them)
    assert "configured_at" in body
    assert "last_rotated_at" in body


async def test_put_credentials_query_id_only_updates_without_ibkr_ping(
    client: AsyncClient, monkeypatch
):
    """PUT with only query_id (no token) does not ping IBKR and updates query_id."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _make_test_key())
    token = await _register_and_login_unique(client, "qidonly@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    success_xml = b"""<?xml version="1.0"?>
<FlexStatementResponse><Status>Success</Status><ReferenceCode>11</ReferenceCode></FlexStatementResponse>"""

    # Create initial credentials with token
    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as mock_router:
        mock_router.get("/Universal/servlet/FlexStatementService.SendRequest").mock(
            return_value=Response(200, content=success_xml)
        )
        await client.put(
            "/api/credentials/flex",
            json={"token": "initial-token-xyz", "query_id": "OLD-QID"},
            headers=headers,
        )

    # Now update only the query_id — no token, no IBKR ping should occur.
    # We use assert_all_called=False so the mock doesn't complain about uncalled routes;
    # if IBKR were hit, the side_effect would raise and the test would fail.
    ibkr_was_pinged = []
    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(
            "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
        ).mock(side_effect=lambda req: ibkr_was_pinged.append(True) or Response(200, content=b""))
        resp = await client.put(
            "/api/credentials/flex",
            json={"query_id": "NEW-QID"},
            headers=headers,
        )

    assert ibkr_was_pinged == [], "IBKR was unexpectedly pinged when updating query_id only"

    assert resp.status_code == 200

    # Verify query_id was updated
    get_resp = await client.get("/api/credentials/flex", headers=headers)
    assert get_resp.json()["query_id"] == "NEW-QID"


async def test_put_credentials_ibkr_unreachable_returns_502(
    client: AsyncClient, monkeypatch
):
    """Network error contacting IBKR during PUT returns 502."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _make_test_key())
    token = await _register_and_login_unique(client, "ibkrtimeout@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as mock_router:
        mock_router.get("/Universal/servlet/FlexStatementService.SendRequest").mock(
            side_effect=httpx.ConnectTimeout("connection timed out")
        )
        resp = await client.put(
            "/api/credentials/flex",
            json={"token": "some-valid-token", "query_id": "1234567"},
            headers=headers,
        )

    assert resp.status_code == 502
    detail = resp.json()["detail"].lower()
    assert "ibkr" in detail or "alcanzar" in detail


async def test_put_credentials_first_time_requires_both_token_and_query_id(
    client: AsyncClient, monkeypatch
):
    """First-time PUT with only query_id (no token) returns 400."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _make_test_key())
    token = await _register_and_login_unique(client, "firsttime@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.put(
        "/api/credentials/flex",
        json={"query_id": "1234567"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "query_id" in resp.json()["detail"].lower() or "token" in resp.json()["detail"].lower()


async def test_put_credentials_first_time_requires_both_only_token(
    client: AsyncClient, monkeypatch
):
    """First-time PUT with only token (no query_id) also returns 400 (missing query_id for ping)."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _make_test_key())
    token = await _register_and_login_unique(client, "firsttimetoken@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    # With only token — no query_id to use for IBKR ping
    # This hits the "not test_query_id → 400" branch in update_flex_credentials
    resp = await client.put(
        "/api/credentials/flex",
        json={"token": "some-valid-token-ab"},
        headers=headers,
    )
    assert resp.status_code == 400


async def test_put_credentials_updates_existing_credentials_token(
    client: AsyncClient, monkeypatch
):
    """PUT with new token on existing credentials validates + rotates the token."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _make_test_key())
    token = await _register_and_login_unique(client, "rotatetoken@test.com")
    headers = {"Authorization": f"Bearer {token}"}

    success_xml = b"""<?xml version="1.0"?>
<FlexStatementResponse><Status>Success</Status><ReferenceCode>77</ReferenceCode></FlexStatementResponse>"""

    # Create initial credentials
    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as mock_router:
        mock_router.get("/Universal/servlet/FlexStatementService.SendRequest").mock(
            return_value=Response(200, content=success_xml)
        )
        r1 = await client.put(
            "/api/credentials/flex",
            json={"token": "original-token-abc", "query_id": "QFIRST"},
            headers=headers,
        )
    assert r1.status_code == 200

    # Rotate with new token
    with respx.mock(base_url="https://gdcdyn.interactivebrokers.com") as mock_router:
        mock_router.get("/Universal/servlet/FlexStatementService.SendRequest").mock(
            return_value=Response(200, content=success_xml)
        )
        r2 = await client.put(
            "/api/credentials/flex",
            json={"token": "rotated-token-xyz", "query_id": "QSECOND"},
            headers=headers,
        )
    assert r2.status_code == 200

    # Verify query_id was updated to new value
    get_resp = await client.get("/api/credentials/flex", headers=headers)
    assert get_resp.json()["query_id"] == "QSECOND"


async def test_put_credentials_requires_auth(client: AsyncClient):
    """PUT /api/credentials/flex without auth returns 401."""
    resp = await client.put(
        "/api/credentials/flex",
        json={"token": "some-valid-token", "query_id": "1234567"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Direct handler tests — bypass ASGI transport to get coverage.py tracing
# on the async handler bodies (sys.settrace doesn't follow ASGI coroutines).
# ---------------------------------------------------------------------------

async def test_get_flex_credentials_handler_404_branch(db_session, sample_user, monkeypatch):
    """Direct call to get_flex_credentials raises 404 when no credentials exist."""
    import pytest
    from ibkr_control.api.credentials import get_flex_credentials

    with pytest.raises(Exception) as exc_info:
        await get_flex_credentials(user=sample_user, session=db_session)

    assert "404" in str(exc_info.value) or "No Flex credentials" in str(exc_info.value)


async def test_get_flex_credentials_handler_200_branch(db_session, sample_user, monkeypatch):
    """Direct call to get_flex_credentials returns FlexCredentialsRead when creds exist."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _make_test_key())

    from ibkr_control.api.credentials import get_flex_credentials
    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from ibkr_control.ingest.flex import crypto as crypto_mod

    creds = FlexCredentials(
        user_id=sample_user.id,
        token_encrypted=crypto_mod.encrypt_token("test-token-abc"),
        ytd_query_id="DIRECT-QID",
    )
    db_session.add(creds)
    await db_session.commit()
    await db_session.refresh(creds)

    result = await get_flex_credentials(user=sample_user, session=db_session)
    assert result.query_id == "DIRECT-QID"
    assert result.last_rotated_at is not None


async def test_update_flex_credentials_handler_first_time_no_token_raises_400(
    db_session, sample_user, monkeypatch
):
    """Direct call to update_flex_credentials: first time, only query_id → 400."""
    import pytest
    from ibkr_control.api.credentials import update_flex_credentials
    from ibkr_control.api._schemas import FlexCredentialsUpdate

    payload = FlexCredentialsUpdate(token=None, query_id="QID-ONLY")

    with pytest.raises(Exception) as exc_info:
        await update_flex_credentials(payload=payload, user=sample_user, session=db_session)

    assert "400" in str(exc_info.value) or "Primera vez" in str(exc_info.value) or "token" in str(exc_info.value).lower()


async def test_update_flex_credentials_handler_token_ping_succeeds(
    db_session, sample_user, monkeypatch
):
    """Direct call to update_flex_credentials: with token + query_id, IBKR ping succeeds → saves."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _make_test_key())

    from ibkr_control.api.credentials import update_flex_credentials
    from ibkr_control.api._schemas import FlexCredentialsUpdate
    from ibkr_control.ingest.flex import client as flex_client_mod

    payload = FlexCredentialsUpdate(token="valid-token-abc123", query_id="QID-DIRECT")

    async def fake_send_request(query_id):
        return "REF-CODE"

    original_init = flex_client_mod.FlexClient.__init__

    class FakeFlexClient:
        def __init__(self, token):
            self.token = token

        async def send_request(self, query_id):
            return "REF-CODE"

    monkeypatch.setattr(flex_client_mod, "FlexClient", FakeFlexClient)

    result = await update_flex_credentials(payload=payload, user=sample_user, session=db_session)
    assert result == {"ok": True}


async def test_update_flex_credentials_handler_ibkr_auth_error_raises_401(
    db_session, sample_user, monkeypatch
):
    """Direct call: IBKR returns FlexAuthError → raises 401."""
    import pytest
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _make_test_key())

    from ibkr_control.api.credentials import update_flex_credentials
    from ibkr_control.api._schemas import FlexCredentialsUpdate
    from ibkr_control.ingest.flex import client as flex_client_mod

    payload = FlexCredentialsUpdate(token="bad-token-abc123", query_id="QID-DIRECT")

    class FakeFlexClientAuthFail:
        def __init__(self, token):
            pass

        async def send_request(self, query_id):
            raise flex_client_mod.FlexAuthError("1018", "Invalid token")

    monkeypatch.setattr(flex_client_mod, "FlexClient", FakeFlexClientAuthFail)

    with pytest.raises(Exception) as exc_info:
        await update_flex_credentials(payload=payload, user=sample_user, session=db_session)

    assert "401" in str(exc_info.value) or "invalido" in str(exc_info.value).lower() or "Invalid" in str(exc_info.value)


async def test_update_flex_credentials_handler_ibkr_network_error_raises_502(
    db_session, sample_user, monkeypatch
):
    """Direct call: network error → raises 502."""
    import pytest
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _make_test_key())

    from ibkr_control.api.credentials import update_flex_credentials
    from ibkr_control.api._schemas import FlexCredentialsUpdate
    from ibkr_control.ingest.flex import client as flex_client_mod

    payload = FlexCredentialsUpdate(token="some-token-abc123", query_id="QID-TIMEOUT")

    class FakeFlexClientTimeout:
        def __init__(self, token):
            pass

        async def send_request(self, query_id):
            raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(flex_client_mod, "FlexClient", FakeFlexClientTimeout)

    with pytest.raises(Exception) as exc_info:
        await update_flex_credentials(payload=payload, user=sample_user, session=db_session)

    assert "502" in str(exc_info.value) or "alcanzar" in str(exc_info.value).lower()


async def test_update_flex_credentials_handler_updates_existing(
    db_session, sample_user, monkeypatch
):
    """Direct call: existing creds, update token only → updates token_encrypted and last_rotated_at."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _make_test_key())

    from ibkr_control.api.credentials import update_flex_credentials
    from ibkr_control.api._schemas import FlexCredentialsUpdate
    from ibkr_control.ingest.flex import client as flex_client_mod
    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from ibkr_control.ingest.flex import crypto as crypto_mod

    # Create initial credentials
    creds = FlexCredentials(
        user_id=sample_user.id,
        token_encrypted=crypto_mod.encrypt_token("original-token"),
        ytd_query_id="ORIGINAL-QID",
    )
    db_session.add(creds)
    await db_session.commit()

    # Update only query_id (no token, no IBKR ping)
    payload = FlexCredentialsUpdate(token=None, query_id="NEW-QID-DIRECT")

    result = await update_flex_credentials(payload=payload, user=sample_user, session=db_session)
    assert result == {"ok": True}

    # Verify query_id was updated
    from sqlalchemy import select
    await db_session.refresh(creds)
    assert creds.ytd_query_id == "NEW-QID-DIRECT"


async def test_update_flex_credentials_handler_token_with_no_query_id_available_raises_400(
    db_session, sample_user, monkeypatch
):
    """Direct call: token provided but no query_id anywhere (no payload, no existing creds) → 400."""
    import pytest
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _make_test_key())

    from ibkr_control.api.credentials import update_flex_credentials
    from ibkr_control.api._schemas import FlexCredentialsUpdate

    # No existing creds in DB — so test_query_id will be None
    # payload has token but no query_id
    payload = FlexCredentialsUpdate(token="some-token-abc123", query_id=None)

    with pytest.raises(Exception) as exc_info:
        await update_flex_credentials(payload=payload, user=sample_user, session=db_session)

    assert "400" in str(exc_info.value) or "query_id" in str(exc_info.value).lower()


async def test_update_flex_credentials_handler_new_token_on_existing(
    db_session, sample_user, monkeypatch
):
    """Direct call: existing creds, provide new token → validates against IBKR and updates."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", _make_test_key())

    from ibkr_control.api.credentials import update_flex_credentials
    from ibkr_control.api._schemas import FlexCredentialsUpdate
    from ibkr_control.ingest.flex import client as flex_client_mod
    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from ibkr_control.ingest.flex import crypto as crypto_mod

    # Create initial credentials
    creds = FlexCredentials(
        user_id=sample_user.id,
        token_encrypted=crypto_mod.encrypt_token("original-token"),
        ytd_query_id="ORIGINAL-QID",
    )
    db_session.add(creds)
    await db_session.commit()

    class FakeFlexClientOk:
        def __init__(self, token):
            pass

        async def send_request(self, query_id):
            return "REF-CODE"

    monkeypatch.setattr(flex_client_mod, "FlexClient", FakeFlexClientOk)

    payload = FlexCredentialsUpdate(token="new-token-abc123", query_id=None)
    result = await update_flex_credentials(payload=payload, user=sample_user, session=db_session)
    assert result == {"ok": True}

    await db_session.refresh(creds)
    # Token was rotated
    assert creds.token_encrypted != crypto_mod.encrypt_token("original-token")
