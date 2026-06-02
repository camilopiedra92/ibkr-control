"""G6: CRUD de grants owner-only + isolation."""

from httpx import AsyncClient


async def test_create_list_delete_grant(client: AsyncClient, auth_headers, second_auth_headers):
    # second_auth_headers registró api_test_2@test.com — es el grantee (contador).
    r = await client.post(
        "/api/grants",
        json={"grantee_email": "api_test_2@test.com", "valid_from": "2026-01-01"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["grantee_email"] == "api_test_2@test.com"
    assert body["grantor_email"] == "api_test@test.com"
    assert body["role"] == "read_only"

    r = await client.get("/api/grants", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()["granted"]) == 1

    # La dirección received del grantee debe mostrar al grantor correcto
    r2 = await client.get("/api/grants", headers=second_auth_headers)
    received = r2.json()["received"]
    assert len(received) == 1
    assert received[0]["grantor_email"] == "api_test@test.com"
    assert received[0]["grantee_email"] == "api_test_2@test.com"

    g = body
    r = await client.request(
        "DELETE",
        f"/api/grants/{g['grantee_user_id']}/{g['valid_from']}",
        headers=auth_headers,
    )
    assert r.status_code == 204
    r = await client.get("/api/grants", headers=auth_headers)
    assert r.json()["granted"] == []


async def test_grant_unknown_email_404(client: AsyncClient, auth_headers):
    r = await client.post(
        "/api/grants",
        json={"grantee_email": "nobody@nowhere.com", "valid_from": "2026-01-01"},
        headers=auth_headers,
    )
    assert r.status_code == 404


async def test_self_grant_400(client: AsyncClient, auth_headers):
    r = await client.post(
        "/api/grants",
        json={"grantee_email": "api_test@test.com", "valid_from": "2026-01-01"},
        headers=auth_headers,
    )
    assert r.status_code == 400
