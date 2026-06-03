async def test_register_creates_user(client):
    response = await client.post(
        "/api/auth/register",
        json={"email": "owner@example.com", "password": "supersecret123", "name": "Test Owner"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "owner@example.com"
    assert body["name"] == "Test Owner"


async def test_login_returns_jwt(client):
    await client.post(
        "/api/auth/register",
        json={"email": "c@x.com", "password": "supersecret123", "name": "C"},
    )
    response = await client.post(
        "/api/auth/jwt/login",
        data={"username": "c@x.com", "password": "supersecret123"},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert token


async def test_me_requires_auth(client):
    await client.post(
        "/api/auth/register",
        json={"email": "c@x.com", "password": "supersecret123", "name": "C"},
    )
    login = await client.post(
        "/api/auth/jwt/login",
        data={"username": "c@x.com", "password": "supersecret123"},
    )
    token = login.json()["access_token"]

    me = await client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "c@x.com"


async def test_me_without_token_is_401(client):
    response = await client.get("/api/users/me")
    assert response.status_code == 401
