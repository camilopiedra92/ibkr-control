"""G6: require_account_scope traduce on_behalf_of a un set o 403."""

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def app_with_scope_route(app_with_db):
    from fastapi import Depends

    from ibkr_control.authz.dependencies import require_account_scope

    # Mutamos la instancia de app que devuelve app_with_db (función-scoped),
    # por eso agregar la ruta probe es seguro: cada test recibe una app nueva.
    # Si app_with_db pasara a un scope más amplio, esto filtraría la ruta.
    @app_with_db.get("/api/_test/scope")
    async def _scope_probe(ids: set[int] = Depends(require_account_scope)):
        return {"ids": sorted(ids)}

    return app_with_db


@pytest.fixture
async def scope_client(app_with_scope_route):
    transport = ASGITransport(app=app_with_scope_route)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def auth_headers(scope_client: AsyncClient) -> dict:
    """Registra un usuario de test contra scope_client y devuelve headers JWT."""
    # No reusamos el auth_headers del conftest: está atado a `client` (otra
    # instancia de app_with_db → otra DB). El JWT debe minarse contra la MISMA
    # app/instancia que monta la ruta scope, si no fastapi-users falla el lookup
    # del user y devolvería 401 (enmascarando el 403 real que queremos testear).
    await scope_client.post(
        "/api/auth/register",
        json={"email": "api_test@test.com", "password": "supersecret123", "name": "API Test User"},
    )
    login = await scope_client.post(
        "/api/auth/jwt/login",
        data={"username": "api_test@test.com", "password": "supersecret123"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_owner_scope_no_participations_returns_empty(scope_client, auth_headers):
    # auth_headers registró un user sin participations -> set vacío, 200.
    r = await scope_client.get("/api/_test/scope", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["ids"] == []


async def test_on_behalf_of_without_grant_is_403(scope_client, auth_headers):
    r = await scope_client.get(
        "/api/_test/scope", params={"on_behalf_of": 999999}, headers=auth_headers
    )
    assert r.status_code == 403
