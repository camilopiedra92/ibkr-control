from httpx import AsyncClient

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
