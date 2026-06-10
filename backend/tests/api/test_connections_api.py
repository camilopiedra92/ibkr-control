"""Tests del router /api/connections (org-scoped, RLS)."""

from datetime import date, datetime

import httpx
from httpx import AsyncClient

from ibkr_control.ingest.flex import client as flex_client_mod


# ---------------------------------------------------------------------------
# Fakes para FlexClient (validacion de token contra IBKR)
# ---------------------------------------------------------------------------


class _FakeFlexClientOk:
    def __init__(self, token):
        self.token = token

    async def send_request(self, query_id):
        return "REF-CODE"


class _FakeFlexClientAuthFail:
    def __init__(self, token):
        pass

    async def send_request(self, query_id):
        raise flex_client_mod.FlexAuthError("1018", "Invalid token")


def _patch_flex_ok(monkeypatch):
    monkeypatch.setattr(flex_client_mod, "FlexClient", _FakeFlexClientOk)


def _patch_flex_auth_fail(monkeypatch):
    monkeypatch.setattr(flex_client_mod, "FlexClient", _FakeFlexClientAuthFail)


async def _create_connection(client, headers, monkeypatch, *, query_id="QID-1", display_name=None):
    _patch_flex_ok(monkeypatch)
    resp = await client.post(
        "/api/connections",
        json={"token": "valid-token-abc123", "query_id": query_id, "display_name": display_name},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_list_connections_empty(client: AsyncClient, auth_headers_with_org: dict):
    resp = await client.get("/api/connections", headers=auth_headers_with_org)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_create_connection_invalid_token_401(
    client: AsyncClient, auth_headers_with_org: dict, monkeypatch
):
    _patch_flex_auth_fail(monkeypatch)
    resp = await client.post(
        "/api/connections",
        json={"token": "bad-token-123456", "query_id": "QID-1"},
        headers=auth_headers_with_org,
    )
    assert resp.status_code == 401
    assert "token" in resp.json()["detail"].lower() or "invalid" in resp.json()["detail"].lower()


async def test_create_connection_ok_201_token_not_leaked(
    client: AsyncClient, auth_headers_with_org: dict, monkeypatch
):
    body = await _create_connection(
        client, auth_headers_with_org, monkeypatch, query_id="QID-CREATE"
    )
    assert body["status"] == "active"
    assert body["institution_code"] == "ibkr"
    assert body["provider_type"] == "ibkr_flex"
    assert body["query_id"] == "QID-CREATE"
    # token must never appear anywhere in the response
    assert "token" not in body
    assert "valid-token-abc123" not in str(body)


async def test_list_connections_returns_created(
    client: AsyncClient, auth_headers_with_org: dict, monkeypatch
):
    await _create_connection(client, auth_headers_with_org, monkeypatch, query_id="QID-LIST")
    resp = await client.get("/api/connections", headers=auth_headers_with_org)
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["query_id"] == "QID-LIST"
    assert "token" not in items[0]
    assert "valid-token-abc123" not in str(items[0])


async def test_patch_connection_display_name(
    client: AsyncClient, auth_headers_with_org: dict, monkeypatch
):
    created = await _create_connection(client, auth_headers_with_org, monkeypatch)
    conn_id = created["id"]
    resp = await client.patch(
        f"/api/connections/{conn_id}",
        json={"display_name": "Mi cuenta IBKR"},
        headers=auth_headers_with_org,
    )
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Mi cuenta IBKR"


async def test_rotate_token_advances_and_clears_reauth(
    client: AsyncClient, auth_headers_with_org: dict, app_owner_engine, monkeypatch
):
    """rotate-token actualiza last_rotated_at y, si la conexion estaba en
    reauth_required, la transiciona a active (mark_rotated)."""
    created = await _create_connection(client, auth_headers_with_org, monkeypatch, query_id="QID-R")
    conn_id = created["id"]
    old_rotated = datetime.fromisoformat(created["last_rotated_at"])

    # Force the connection into reauth_required directly in the DB (as owner).
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    session_maker = async_sessionmaker(
        app_owner_engine, expire_on_commit=False, class_=AsyncSession
    )
    async with session_maker() as session:
        await session.execute(
            text("UPDATE connections SET status = 'reauth_required' WHERE id = :i").bindparams(
                i=conn_id
            )
        )
        await session.commit()

    _patch_flex_ok(monkeypatch)
    resp = await client.post(
        f"/api/connections/{conn_id}/rotate-token",
        json={"token": "rotated-token-xyz1"},
        headers=auth_headers_with_org,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "active"
    new_rotated = datetime.fromisoformat(body["last_rotated_at"])
    assert new_rotated > old_rotated
    assert "token" not in body
    assert "rotated-token-xyz1" not in str(body)


async def test_disable_then_enable(client: AsyncClient, auth_headers_with_org: dict, monkeypatch):
    created = await _create_connection(client, auth_headers_with_org, monkeypatch)
    conn_id = created["id"]

    disabled = await client.post(
        f"/api/connections/{conn_id}/disable", headers=auth_headers_with_org
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    enabled = await client.post(f"/api/connections/{conn_id}/enable", headers=auth_headers_with_org)
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "active"


async def test_delete_connection_sets_import_connection_id_null(
    client: AsyncClient, auth_headers_with_org: dict, app_owner_engine, monkeypatch
):
    created = await _create_connection(client, auth_headers_with_org, monkeypatch)
    conn_id = created["id"]

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    session_maker = async_sessionmaker(
        app_owner_engine, expire_on_commit=False, class_=AsyncSession
    )

    # Insert a flex_import linked to the connection (need its org_id first).
    async with session_maker() as session:
        org_id = await session.scalar(
            text("SELECT organization_id FROM connections WHERE id = :i").bindparams(i=conn_id)
        )
        from ibkr_control.db.models.flex_raw import FlexImport

        await session.execute(
            text("SELECT set_config('app.current_org', :o, true)").bindparams(o=str(org_id))
        )
        fi = FlexImport(
            organization_id=org_id,
            connection_id=conn_id,
            anyo=2025,
            xml_hash="del-test-hash",
            xml_size_bytes=10,
            xml_bytes=b"<x/>",
            source="web_service",
            period_covered_from=date(2025, 1, 1),
            period_covered_to=date(2025, 12, 31),
            year_status="rolling",
            status="ok",
        )
        session.add(fi)
        await session.commit()
        import_id = fi.id

    resp = await client.delete(f"/api/connections/{conn_id}", headers=auth_headers_with_org)
    assert resp.status_code == 204

    async with session_maker() as session:
        remaining = await session.scalar(
            text("SELECT connection_id FROM flex_imports WHERE id = :i").bindparams(i=import_id)
        )
        # row still exists; FK was SET NULL
        assert remaining is None
        still_there = await session.scalar(
            text("SELECT count(*) FROM flex_imports WHERE id = :i").bindparams(i=import_id)
        )
        assert still_there == 1


async def test_rls_isolation_org_b_cannot_see_org_a(
    client: AsyncClient,
    auth_headers_with_org: dict,
    second_auth_headers_with_org: dict,
    monkeypatch,
):
    """Org B no ve ni opera la connection de Org A: 404 (no 403, no filtra
    existencia)."""
    created = await _create_connection(client, auth_headers_with_org, monkeypatch, query_id="QID-A")
    conn_id = created["id"]

    # Org B's list does not include org A's connection.
    list_b = await client.get("/api/connections", headers=second_auth_headers_with_org)
    assert list_b.status_code == 200
    assert list_b.json() == []

    # Org B cannot patch / rotate / disable / delete org A's connection -> 404.
    patch_b = await client.patch(
        f"/api/connections/{conn_id}",
        json={"display_name": "hijack"},
        headers=second_auth_headers_with_org,
    )
    assert patch_b.status_code == 404

    disable_b = await client.post(
        f"/api/connections/{conn_id}/disable", headers=second_auth_headers_with_org
    )
    assert disable_b.status_code == 404

    delete_b = await client.delete(
        f"/api/connections/{conn_id}", headers=second_auth_headers_with_org
    )
    assert delete_b.status_code == 404


async def test_connections_require_auth(client: AsyncClient):
    """Sin auth -> 401 (org_context depende de current_active_user)."""
    resp = await client.get("/api/connections")
    assert resp.status_code == 401


async def test_create_connection_403_when_no_org(client: AsyncClient, auth_headers: dict):
    """Usuario sin membership no resuelve org_context -> 403."""
    resp = await client.post(
        "/api/connections",
        json={"token": "valid-token-abc123", "query_id": "QID-1"},
        headers=auth_headers,
    )
    assert resp.status_code == 403


async def test_create_connection_ibkr_unreachable_502(
    client: AsyncClient, auth_headers_with_org: dict, monkeypatch
):
    class _FakeFlexClientTimeout:
        def __init__(self, token):
            pass

        async def send_request(self, query_id):
            raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(flex_client_mod, "FlexClient", _FakeFlexClientTimeout)
    resp = await client.post(
        "/api/connections",
        json={"token": "some-valid-token", "query_id": "QID-1"},
        headers=auth_headers_with_org,
    )
    assert resp.status_code == 502
