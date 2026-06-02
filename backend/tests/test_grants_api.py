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


async def test_contador_isolation_end_to_end(app_with_db):
    """A otorga read a C; C ve los accounts de A vía on_behalf_of; sin grant, 403."""
    from datetime import date

    from fastapi import Depends
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select

    from ibkr_control.auth.models import User
    from ibkr_control.authz.dependencies import require_account_scope
    from ibkr_control.db.models.accounts import Account
    from ibkr_control.db.models.participations import Participation
    from ibkr_control.db.session import get_async_session

    @app_with_db.get("/api/_test/scope")
    async def _scope_probe(ids: set[int] = Depends(require_account_scope)):
        return {"ids": sorted(ids)}

    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # Registrar owner (A) y contador (C)
        await c.post(
            "/api/auth/register",
            json={"email": "a@t.com", "password": "supersecret123", "name": "A"},
        )
        await c.post(
            "/api/auth/register",
            json={"email": "c@t.com", "password": "supersecret123", "name": "C"},
        )
        a_tok = (
            await c.post(
                "/api/auth/jwt/login",
                data={"username": "a@t.com", "password": "supersecret123"},
            )
        ).json()["access_token"]
        c_tok = (
            await c.post(
                "/api/auth/jwt/login",
                data={"username": "c@t.com", "password": "supersecret123"},
            )
        ).json()["access_token"]
        a_h = {"Authorization": f"Bearer {a_tok}"}
        c_h = {"Authorization": f"Bearer {c_tok}"}

        # Sembrar un account + participation de A directamente en DB
        session_gen = app_with_db.dependency_overrides[get_async_session]()
        session = await session_gen.__anext__()
        acc = Account(ibkr_account_id="U22222222", alias="a-acc", currency="USD")
        session.add(acc)
        await session.commit()
        await session.refresh(acc)
        a_id = await session.scalar(select(User.id).where(User.email == "a@t.com"))
        session.add(
            Participation(user_id=a_id, account_id=acc.id, pct=1, valid_from=date(2020, 1, 1))
        )
        await session.commit()

        # Sin grant: C con on_behalf_of=A -> 403
        r = await c.get("/api/_test/scope", params={"on_behalf_of": a_id}, headers=c_h)
        assert r.status_code == 403

        # A otorga grant a C
        r = await c.post(
            "/api/grants",
            json={"grantee_email": "c@t.com", "valid_from": "2020-01-01"},
            headers=a_h,
        )
        assert r.status_code == 201

        # Ahora C ve el account de A
        r = await c.get("/api/_test/scope", params={"on_behalf_of": a_id}, headers=c_h)
        assert r.status_code == 200
        assert r.json()["ids"] == [acc.id]

        # C sin contexto -> set vacío (no ve nada propio)
        r = await c.get("/api/_test/scope", headers=c_h)
        assert r.json()["ids"] == []
