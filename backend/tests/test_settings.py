from httpx import AsyncClient
from sqlalchemy import text

from ibkr_control.db.session import get_async_session

# Fixtures (client, app_with_db, postgres_container) viven en tests/conftest.py.


async def _register_and_login(client: AsyncClient) -> str:
    await client.post(
        "/api/auth/register",
        json={"email": "c@x.com", "password": "supersecret123", "name": "C"},
    )
    login = await client.post(
        "/api/auth/jwt/login",
        data={"username": "c@x.com", "password": "supersecret123"},
    )
    return login.json()["access_token"]


async def test_settings_auto_created_on_register(client):
    token = await _register_and_login(client)
    response = await client.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["marginal_rate"] == "0.3900"
    assert body["timezone"] == "America/Bogota"


async def test_settings_patch_marginal_rate(client):
    token = await _register_and_login(client)
    response = await client.patch(
        "/api/settings",
        json={"marginal_rate": "0.3300"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["marginal_rate"] == "0.3300"


async def test_settings_patch_rejects_out_of_range(client):
    token = await _register_and_login(client)
    response = await client.patch(
        "/api/settings",
        json={"marginal_rate": "1.5000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


async def test_settings_requires_auth(client):
    response = await client.get("/api/settings")
    assert response.status_code == 401


async def test_settings_patch_accepts_valid_timezone(client):
    token = await _register_and_login(client)
    response = await client.patch(
        "/api/settings",
        json={"timezone": "Europe/Madrid"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["timezone"] == "Europe/Madrid"


async def test_settings_patch_rejects_invalid_timezone(client):
    # Prerequisito de Phase 2: APScheduler crashea silenciosamente al boot si
    # el TZ no es reconocido por zoneinfo. Validar en la frontera (422), no
    # en runtime.
    token = await _register_and_login(client)
    response = await client.patch(
        "/api/settings",
        json={"timezone": "Foo/Bar"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


async def test_settings_patch_rejects_excess_decimal_places(client):
    # max_digits=5, decimal_places=4 en el schema debe rechazar 5+ decimales
    # antes de pegarle a Postgres, que de otro modo redondea silenciosamente.
    token = await _register_and_login(client)
    response = await client.patch(
        "/api/settings",
        json={"marginal_rate": "0.12345"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


async def test_settings_get_recovers_when_row_missing(client, app_with_db):
    # Protege la deviation 1: _load es idempotente. Si on_after_register fallara
    # post-User.commit, el row de UserSettings no existiría. GET debe recrearlo
    # con defaults DB-side, no 404. Sin este test, refactorizar _load a strict
    # 404 pasaría silenciosamente (los otros tests no ejercen la rama de None).
    token = await _register_and_login(client)
    override = app_with_db.dependency_overrides[get_async_session]
    async for session in override():
        await session.execute(text("DELETE FROM user_settings"))
        await session.commit()
        break

    response = await client.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["marginal_rate"] == "0.3900"
    assert body["timezone"] == "America/Bogota"
